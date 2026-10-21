"""Run the out-of-tree findings gate over a corpus directory.

New code.  ``scripts/gate/verify_findings.py`` is the gate, and it is genuinely
out of tree -- ``templates/tend/config.yaml.tmpl`` invokes it on the run host and
``run/manifest.py`` rsyncs it to its own directory there.  But nothing the skill
could execute locally ever ran it: ``cf harvest`` and ``cf import-corpus`` wrote
a corpus with a completeness receipt and no schema or quote validation at all,
so the only automated invocation lived in a tend config that has never executed.

This module closes that: ``cf harvest --gate`` / ``cf import-corpus --gate`` call
:func:`gate_corpus` on what landed, and the RECEIPT is not complete unless it
passed.  It reuses ``verify_findings``'s own functions rather than shelling out,
so a gate failure is a list of problems the receipt can carry.

A locate corpus is re-gated by the run's OWN gate: the copy its plan staged in
``runs/<id>/tend-root/gate/``, each file checked against ``run.yaml``
``locate.gate_files`` and then loaded from its sources apart from the
checkout's modules (:func:`gate_locate_corpus`), so a gate edited or upgraded
since the plan never judges the corpus its workers were judged by; the
receipt's ``gate_code`` names that gate by hash.  The config it reads and the
task manifests that config names are held to the plan's own records of them
(:func:`run_config_problems`: the staged ``gate.json``, ``run.yaml``,
``planned.json``), so the pins the gate compares are the plan's
(references/locate-documents.md section 10).
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - import side effect
    sys.path.insert(0, str(_SCRIPTS))

from gate import verify_findings as vf  # noqa: E402

from harvest._compat import CfError, finding_name, hashing, iter_finding_files  # noqa: E402

#: A harvested corpus keeps its findings directly in ``corpus/<run-id>/``, not in
#: a ``findings/`` subdirectory the way a worker's worktree does.
CORPUS_FINDINGS_DIR = "."


def gate_corpus(dest: str | Path, gate_config: str | Path,
                findings_dir: str = CORPUS_FINDINGS_DIR) -> dict:
    """Validate every findings file in *dest* against *gate_config*.

    Returns ``{ok, config, preset, files, problems, partial}``; ``problems`` is
    the same list ``verify_findings.main`` would print, so the receipt records
    exactly what the gate said.

    An EMPTY directory is a gate failure, not a pass: a gate asked to validate a
    corpus and given nothing to validate has not approved anything, and
    ``files: 0, problems: [], ok: True`` is how an empty corpus receipted as
    complete.  The rule lives here rather than in ``cf harvest``'s wrapper so
    that every caller inherits it -- the tests call this function directly, and
    so would any future one.
    """
    dest = Path(dest)
    cfg_path = Path(gate_config).expanduser()
    if not cfg_path.exists():
        raise CfError(f"no gate config at {cfg_path}",
                      hint="`cf plan-run` writes runs/<id>/gate.local.json; pass --gate-config")
    cfg = vf.GateConfig(json.loads(cfg_path.read_text(encoding="utf-8")), cfg_path)
    cfg.findings_dir = findings_dir

    books, problems = vf.load_sources(cfg)
    decl_index = vf._load_decl_index(cfg.decl_index) if cfg.decl_index else None

    # The receipt's own listing (recursive, harvest/files.py), so every file the
    # receipt and the tally count is a file this gate validated; a top-level
    # glob left a nested file counted and never read.
    base = dest / cfg.findings_dir
    files = iter_finding_files(base)
    partial: list[str] = []
    notes: list[str] = []
    for f in files:
        rel = finding_name(f, base)
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - any parse failure is a gate failure
            problems.append(f"{rel}: not valid JSON ({exc})")
            continue
        problems.extend(vf.validate_doc(doc, rel, cfg, books, decl_index))
        book = vf._book_for(books, vf.doc_source_id(doc, cfg))
        partial.extend(vf.partial_quotes(doc, rel, book, max_chars=cfg.max_quote_chars,
                                         max_pages=cfg.max_match_pages))
        notes.extend(vf.binding_notes(doc, rel, cfg, book))
    if not files:
        problems.append(f"{dest}: the gate found no findings to validate (an empty corpus is "
                        f"not a clean one)")
    return {
        "ok": not problems,
        "config": str(cfg_path),
        "preset": cfg.preset or cfg.mode,
        "sources": cfg.source_ids(),
        "files": len(files),
        "problems": problems,
        "partial_quotes": partial,
        # A quote the gate could not bind to its item's page, and a quote longer
        # than the copyright rule allows, are neither passes nor failures: the
        # receipt records them so "the corpus is gated" never means "nobody
        # looked at the ones it could not check".
        "binding_notes": notes,
        "unbound_quotes": sum(1 for n in notes if n.startswith("page unbound:")),
    }


#: where a gate directory keeps its vendored packages (``verify_locate.VENDOR``)
_VENDOR_DIR = "vendor"
#: the staged file of a run's gate directory that is no gate code: the host config,
#: which the config the re-gate reads is checked against (:func:`run_config_problems`)
_HOST_CONFIG = "gate/gate.json"
_NOT_GATE_CODE = (_HOST_CONFIG,)
#: what Python can import from a directory on its path
_IMPORTABLE = frozenset({".py", ".pyc", ".pyo", ".pyd", ".so", ".pth"})
#: the fields of a gate.json that name one layout's paths (``locate.plan.host_layout``):
#: the host's gate.json and its local twin, gate.local.json, differ in these and in
#: nothing else, since every pin is written once for both
LAYOUT_FIELDS = (("corpus_db", "path"), ("tools_corpus_db", "path"),
                 ("sourceset", "sources_dir"), ("candidates_dir",), ("tasks_dir",),
                 ("log_dir",), ("orchestrator_db",))
#: what settles a run whose own gate copy cannot be told from an edited one
CHECKOUT_GATE_HINT = ("`cf harvest <run-id> --checkout-gate` re-gates with this checkout's "
                      "gate instead, and the receipt's gate_code says so")
#: what settles a gate config or a task manifest that is not the one the plan wrote
REPLAN_HINT = ("put back the file the plan wrote, or re-plan the run (`cf plan-locate`) and "
               "run it again")
_ABSENT = object()


class GateLoadError(Exception):
    """A run's own gate copy that does not import: the gate could not run."""


def gate_module_names(gate_dir: Path) -> tuple[frozenset[str], frozenset[str]]:
    """``(modules, packages)`` that a gate directory's copy imports by its own top-level names.

    Read off the directory itself -- each ``<name>.py`` beside ``verify_locate.py``
    and each package under ``vendor/`` -- so a sibling the planner stages is set
    aside with the others and there is no second list to keep in step with
    ``locate.tend_root.GATE_FILES``.
    """
    gate_dir = Path(gate_dir)
    modules = {p.stem for p in gate_dir.glob("*.py") if p.is_file()} | {"verify_locate"}
    vendor = gate_dir / _VENDOR_DIR
    packages = ({p.name for p in vendor.iterdir() if p.is_dir() and p.name != "__pycache__"}
                if vendor.is_dir() else set())
    return frozenset(modules), frozenset(packages)


def _is_gate_module(name: str, names: tuple[frozenset[str], frozenset[str]]) -> bool:
    modules, packages = names
    return name in modules or name.split(".")[0] in packages


@contextlib.contextmanager
def _isolated_gate(gate_dir: Path):
    """Import ``<gate_dir>/verify_locate.py`` with its siblings and vendored packages.

    A gate directory is self-contained (references/locate-documents.md section 9:
    every module it imports is the standard library or a file of the directory),
    so the copy runs as it ran on the host: the checkout's modules of the same
    names (:func:`gate_module_names`) are set aside for the duration and put back
    after, with ``sys.path``.  Only the copy's sources are pinned, so it runs from
    them: the import writes no ``__pycache__`` into the run's directory and reads
    none from it -- bytecode is looked up under an empty directory of its own, so
    a planted ``.pyc`` whose header claims a pinned source's mtime and size never
    runs in place of that source.
    """
    names = gate_module_names(gate_dir)
    saved = {name: module for name, module in sys.modules.items() if _is_gate_module(name, names)}
    for name in saved:
        del sys.modules[name]
    saved_path = list(sys.path)
    saved_bytecode = sys.dont_write_bytecode
    saved_prefix = sys.pycache_prefix
    no_bytecode = tempfile.mkdtemp(prefix="cf-gate-pycache-")
    sys.path[:0] = [str(gate_dir), str(gate_dir / _VENDOR_DIR)]
    sys.dont_write_bytecode = True
    sys.pycache_prefix = no_bytecode
    importlib.invalidate_caches()
    try:
        spec = importlib.util.spec_from_file_location("verify_locate", gate_dir / "verify_locate.py")
        if spec is None or spec.loader is None:
            raise GateLoadError(f"{gate_dir / 'verify_locate.py'} cannot be imported")
        module = importlib.util.module_from_spec(spec)
        sys.modules["verify_locate"] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - any failure to import is one failure
            raise GateLoadError(f"the run's own gate at {gate_dir} did not load "
                                f"({type(exc).__name__}: {exc})") from None
        yield module
    finally:
        for name in [n for n in sys.modules if _is_gate_module(n, names)]:
            del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = saved_path
        sys.dont_write_bytecode = saved_bytecode
        sys.pycache_prefix = saved_prefix
        shutil.rmtree(no_bytecode, ignore_errors=True)


def run_gate_code(gate_dir: Path, gate_files: dict | None) -> tuple[dict, list[str]]:
    """``(gate_code block, problems)``: the run's own gate copy, checked against its pins.

    ``gate_files`` is ``run.yaml`` ``locate.gate_files`` (``{path under the tend
    root: sha256}``, what the planner staged); every ``gate/`` file but the host
    config must be on disk as pinned, so the gate that re-judges the corpus is
    the one the workers ran, not one edited since.  The block records the gate
    script's sha256 and ``files_sha256``, the sha256 of the sorted
    ``<path>\\t<sha256>`` lines of the files checked: the receipt's record of
    which gate judged the corpus.
    """
    script = gate_dir / "verify_locate.py"
    block = {"from": "run", "dir": str(gate_dir), "sha256": None, "files_sha256": None,
             "files": 0}
    pins = {rel: sha for rel, sha in sorted((gate_files or {}).items())
            if rel.startswith("gate/") and rel not in _NOT_GATE_CODE}
    if not pins:
        return block, [f"run.yaml records no locate.gate_files for {gate_dir}, so the run's own "
                       f"gate cannot be told from an edited one"]
    problems = []
    for rel, sha in pins.items():
        path = gate_dir.parent / rel
        if not path.is_file():
            problems.append(f"the run's gate file {rel} is missing from {gate_dir.parent}")
        elif hashing.sha256_file(path) != sha:
            problems.append(f"the run's gate file {rel} is not the bytes its plan staged "
                            f"(run.yaml locate.gate_files)")
    if f"gate/{script.name}" not in pins:
        problems.append(f"run.yaml locate.gate_files pins no gate/{script.name}")
    # The copy is imported with its directory first on the path, as on the host, so a
    # module the plan never staged there (a planted json.py) would run with it; any
    # other stray file (a desktop's .DS_Store) imports nothing and is let be, and so
    # is __pycache__/, whose bytecode the import never reads (:func:`_isolated_gate`).
    for path in sorted(gate_dir.rglob("*")) if gate_dir.is_dir() else []:
        rel = path.relative_to(gate_dir.parent).as_posix()
        if path.is_dir() or path.suffix not in _IMPORTABLE or rel in pins \
                or "__pycache__" in path.parts:
            continue
        problems.append(f"the run's gate directory holds {rel}, which its plan did not stage")
    if problems:
        return block, problems
    lines = "".join(f"{rel}\t{sha}\n" for rel, sha in pins.items())
    block.update(sha256=pins[f"gate/{script.name}"], files=len(pins),
                 files_sha256=hashlib.sha256(lines.encode("utf-8")).hexdigest())
    return block, []


def _without_layout(doc: dict) -> dict:
    """A gate.json less the fields that name one layout's paths (:data:`LAYOUT_FIELDS`)."""
    out = json.loads(json.dumps(doc))
    for *parents, leaf in LAYOUT_FIELDS:
        node = out
        for key in parents:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(leaf, None)
    return out


def _differing(a, b, at: str = "") -> list[str]:
    """The dotted keys at which two parsed JSON documents differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for key in sorted(set(a) | set(b), key=str):
            out += _differing(a.get(key, _ABSENT), b.get(key, _ABSENT),
                              f"{at}.{key}" if at else str(key))
        return out
    return [] if a == b else [at or "the whole document"]


def _few(names: list[str], limit: int = 5) -> str:
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit
                                       else "")


def _manifest_problems(config: dict, planned: Path) -> list[str]:
    """Each task manifest in the config's ``tasks_dir`` is the one ``planned.json`` records.

    A missing directory is the gate's own refusal (it names it), so it is not
    repeated here.
    """
    try:
        doc = json.loads(planned.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{planned} is not readable JSON ({exc})"]
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list):
        return [f"{planned} records no task list"]
    want = {str(t.get("task_id")): t for t in tasks if isinstance(t, dict)}
    tasks_dir = Path(str(config.get("tasks_dir") or ""))
    if not config.get("tasks_dir") or not tasks_dir.is_dir():
        return []
    found: dict[str, object] = {}
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            found[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found[path.stem] = _ABSENT
    problems = []
    edited = sorted(t for t in want if t in found and found[t] != want[t])
    missing = sorted(set(want) - set(found))
    extra = sorted(set(found) - set(want))
    if edited:
        problems.append(f"the task manifest(s) {_few(edited)} in {tasks_dir} are not the ones "
                        f"{planned.name} records (the plan's own)")
    if missing:
        problems.append(f"the task manifest(s) {_few(missing)} that {planned.name} records are "
                        f"missing from {tasks_dir}")
    if extra:
        problems.append(f"{tasks_dir} holds task manifest(s) {_few(extra)} that "
                        f"{planned.name} does not record")
    return problems


def run_config_problems(cfg_path: Path, *, run_doc: dict | None = None,
                        planned: Path | None = None, gate_dir: Path | None = None,
                        gate_files: dict | None = None) -> list[str]:
    """What makes the config a locate re-gate reads, or the manifests it names, not the plan's.

    The gate holds the corpus to the config's pins (``corpus_db.sha256``,
    ``candidates_sha256``, each source's) and hands each task the curated hits
    its manifest names, so a pin or a manifest edited since the plan would let
    the re-gate pass what the workers' gate refused.  Each check is against a
    record of the plan that is not the config itself:

    1. ``corpus_db.sha256`` is ``run.yaml`` ``locate.universe.corpus_db_sha256``,
       when ``run_doc`` records one;
    2. with the run's own gate (``gate_dir``, and ``gate_files`` pinning it), the
       host ``gate.json`` the plan staged beside it is the bytes
       ``locate.gate_files`` pins, and the config is that file but for its
       layout's paths (:data:`LAYOUT_FIELDS`);
    3. each task manifest in the config's ``tasks_dir`` is the one ``planned``
       (``runs/<id>/planned.json``) records, and each planned task has one.  With
       the run's own gate a missing ``planned.json`` is a problem (the planner has
       written it since it first wrote ``gate_files``); with this checkout's
       (``gate_dir`` None) the manifests are then read as found.

    A config that does not parse is the gate's own refusal, so it is not one here.
    """
    try:
        config = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(config, dict):
        return []
    name = Path(cfg_path).name
    problems: list[str] = []
    locate = (run_doc or {}).get("locate") if isinstance(run_doc, dict) else None
    universe = locate.get("universe") if isinstance(locate, dict) else None
    planned_db = universe.get("corpus_db_sha256") if isinstance(universe, dict) else None
    corpus = config.get("corpus_db") if isinstance(config.get("corpus_db"), dict) else {}
    if planned_db and corpus.get("sha256") != planned_db:
        problems.append(f"{name} pins corpus.sqlite {str(corpus.get('sha256'))[:12]}…, run.yaml "
                        f"locate.universe.corpus_db_sha256 {str(planned_db)[:12]}… (not the "
                        f"database this run was planned with)")
    if gate_dir is not None and gate_files:
        host = Path(gate_dir).parent / _HOST_CONFIG
        pin = gate_files.get(_HOST_CONFIG)
        if not pin:
            problems.append(f"run.yaml locate.gate_files pins no {_HOST_CONFIG}, so {name} "
                            f"cannot be checked against the config the plan staged")
        elif not host.is_file():
            problems.append(f"the run's {_HOST_CONFIG} is missing from {host.parent.parent}")
        elif hashing.sha256_file(host) != pin:
            problems.append(f"the run's {_HOST_CONFIG} is not the bytes its plan staged "
                            f"(run.yaml locate.gate_files)")
        else:
            try:
                staged = json.loads(host.read_text(encoding="utf-8"))
            except ValueError:
                staged = None
            differ = _differing(_without_layout(staged) if isinstance(staged, dict) else None,
                                _without_layout(config))
            if differ:
                problems.append(f"{name} is not the {_HOST_CONFIG} its plan staged but for the "
                                f"layout's paths: {_few(differ)} differ")
    if planned is not None and Path(planned).is_file():
        problems += _manifest_problems(config, Path(planned))
    elif gate_dir is not None and gate_files and planned is not None:
        problems.append(f"{planned} is missing, so the task manifests the gate reads cannot be "
                        f"told from edited ones")
    return problems


def checkout_gate_code() -> dict:
    """The ``gate_code`` block of a re-gate by this checkout's gate (``--checkout-gate``)."""
    script = _SCRIPTS / "gate" / "verify_locate.py"
    return {"from": "checkout", "dir": str(script.parent),
            "sha256": hashing.sha256_file(script)}


def _not_run(cfg_path: Path, block: dict, problems: list[str], hint: str) -> dict:
    """A locate gate result for a gate that did not run, with what settles it."""
    return {"ok": False, "config": str(cfg_path), "preset": "locate", "sources": [],
            "files": 0, "problems": [f"the gate could not run: {p}" for p in problems],
            "partial_quotes": [], "binding_notes": [], "unbound_quotes": 0,
            "broken": list(problems), "gate_code": block, "hint": hint}


def gate_locate_corpus(dest: str | Path, gate_config: str | Path, *,
                       gate_dir: str | Path | None = None,
                       gate_files: dict | None = None,
                       run_doc: dict | None = None,
                       planned: str | Path | None = None) -> dict:
    """Run the LOCATE gate (``gate/verify_locate.py``) over a landed locate corpus.

    The corpus keeps ``<task-id>/<source-id>.json`` at its top, so the gate runs in
    directory mode with ``--findings-dir .``, in-process, and returns the shape
    :func:`gate_corpus` returns so the receipt and :func:`render` read it alike.
    An empty corpus fails here too: the gate refuses an empty set itself.

    With ``gate_dir`` -- the run's own gate directory, ``runs/<id>/tend-root/gate``
    -- the gate that runs is that copy, the one planned into the run, after each
    of its files is checked against ``gate_files`` (:func:`run_gate_code`); the
    result's ``gate_code`` names it by hash, and a copy that is not the planned
    bytes fails the gate without running.  Without it, the checkout's gate runs
    (``gate_code.from: checkout``).  Either way the config and the task manifests
    are first checked against the plan's own record of them, ``run_doc``
    (run.yaml) and ``planned`` (planned.json) (:func:`run_config_problems`).  A
    gate that did not run says so in ``broken`` and names what settles it in
    ``hint``.
    """
    cfg_path = Path(gate_config).expanduser()
    if not cfg_path.exists():
        raise CfError(f"no gate config at {cfg_path}",
                      hint="the locate planner writes runs/<id>/gate.local.json; pass --gate-config")
    gate_dir = Path(gate_dir) if gate_dir is not None else None
    config_problems = run_config_problems(cfg_path, run_doc=run_doc,
                                          planned=Path(planned) if planned else None,
                                          gate_dir=gate_dir, gate_files=gate_files)
    if gate_dir is None:
        block = checkout_gate_code()
        if config_problems:
            return _not_run(cfg_path, block, config_problems, REPLAN_HINT)
        from gate import verify_locate as vl

        return _gate_locate_with(vl, dest, cfg_path, block)
    block, code_problems = run_gate_code(gate_dir, gate_files)
    if code_problems or config_problems:
        hint = "; or ".join(h for h, problems in ((CHECKOUT_GATE_HINT, code_problems),
                                                  (REPLAN_HINT, config_problems)) if problems)
        return _not_run(cfg_path, block, code_problems + config_problems, hint)
    try:
        with _isolated_gate(gate_dir) as vl:
            return _gate_locate_with(vl, dest, cfg_path, block)
    except GateLoadError as exc:
        return _not_run(cfg_path, block, [str(exc)], CHECKOUT_GATE_HINT)


def _gate_locate_with(vl, dest: str | Path, cfg_path: Path, gate_code: dict) -> dict:
    """:func:`gate_locate_corpus` with the gate module ``vl`` (the run's or the checkout's)."""
    try:
        cfg = vl.GateConfig.load(cfg_path)
    except vl.GateBroken as exc:
        return {"ok": False, "config": str(cfg_path), "preset": "locate", "sources": [],
                "files": 0, "problems": [f"the gate could not run: {exc}"], "partial_quotes": [],
                "binding_notes": [], "unbound_quotes": 0, "broken": [str(exc)],
                "gate_code": gate_code}
    # A whole-corpus re-gate is not a worker's attempt: its line in the gate log
    # (task_id "", every document) would sit beside the workers' own and be
    # read as one by the bench and the watchdog.  The host's log comes home
    # separately, to runs/<run>/host/gate.jsonl.
    cfg.log_dir = None
    result = vl.run_gate(cfg, root=Path(dest), findings_dir=CORPUS_FINDINGS_DIR)
    problems = [f"the gate could not run: {b}" for b in result["broken"]] + result["problems"]
    # Backstop: every file the receipt counts is one the gate read, or named.
    read = {name for _path, name, _task in vl._documents_in(Path(dest) / CORPUS_FINDINGS_DIR)}
    unread = [name for name in (finding_name(p, dest) for p in iter_finding_files(dest))
              if name not in read and not any(name in line for line in problems)]
    problems += [f"{name}: not a <task-id>/<source-id>.json document; the gate did not "
                 f"validate it" for name in unread]
    out = {"ok": result["exit"] == vl.EXIT_OK and not unread, "config": str(cfg_path),
           "preset": cfg.preset,
           "mode": cfg.mode, "sources": sorted(cfg.sources), "files": result["documents"],
           "problems": problems, "by_rule": result["by_rule"], "broken": result["broken"],
           "partial_quotes": [], "binding_notes": [], "unbound_quotes": 0,
           "gate_code": gate_code}
    if cfg.mode == vl.LOCATE_INVENTORY:
        out["skipped"] = skipped_summary(result.get("stats") or [])
        # the statement cut's counts, only for a run whose gate.json names rule 10 (and a
        # gate that knows it): a corpus of an earlier run gets the receipt it always got
        if getattr(cfg, "statement_cut_rule", 0):
            out["cut"] = cut_summary(result.get("stats") or [])
    return out


#: the counts of the receipt's ``cut`` block (docs/proposals/statement-cut.md section 3.3)
CUT_COUNTS = ("passages", "formal", "compound", "conjuncts", "sentences", "displays",
              "multi_sentence", "multi_display")


def cut_summary(stats: list[dict]) -> dict:
    """What an inventory corpus's cut looks like, under ``statement_cut_rule`` (gate rule 10).

    Totals and, by source, the passages, the ``formal_math`` ones, the compound
    statements, their conjuncts and their shapes (``by_shape``), and over the
    formal passages their sentences and displays (``locate_text.units``; ``math``
    blocks) and how many hold more than one of each.  A formal passage's clauses
    are its conjuncts, or one for a simple statement, so the clauses of the cut
    are ``conjuncts + formal - compound``.  Reported, never a refusal: rule 10
    refuses nothing on a passage's size, and these are what the inventory pilot
    measures.
    """
    total: dict = {k: 0 for k in CUT_COUNTS}
    total["by_shape"] = {}
    by_source: dict[str, dict] = {}
    for row in stats:
        got = row.get("cut") if isinstance(row, dict) else None
        if not isinstance(got, dict):
            continue
        mine = by_source.setdefault(str(row.get("source_id") or ""),
                                    {**{k: 0 for k in CUT_COUNTS}, "by_shape": {}})
        for k in CUT_COUNTS:
            value = int(got.get(k) or 0)
            mine[k] += value
            total[k] += value
        for shape, n in sorted((got.get("by_shape") or {}).items()):
            for into in (mine, total):
                into["by_shape"][shape] = into["by_shape"].get(shape, 0) + int(n or 0)
    for into in (total, *by_source.values()):
        into["by_shape"] = dict(sorted(into["by_shape"].items()))
    return {**total, "by_source": dict(sorted(by_source.items()))}


def skipped_summary(stats: list[dict]) -> dict:
    """What an inventory corpus's skips removed: the coverage floor's use, made visible.

    Gate rule 8 accepts a skip with any reason in place of a finder's passage or
    a math paragraph, and nothing downstream reads ``skips``; so the receipt's
    gate block counts, in total and by source, the skips, the layer characters
    they cover, the math paragraphs covered by a skip and by no passage, and the
    deterministic finders' passages dropped under a skip.
    """
    keys = ("skips", "chars", "math_paragraphs", "finder_passages")
    total = {k: 0 for k in keys}
    by_source: dict[str, dict] = {}
    for row in stats:
        got = row.get("skipped") if isinstance(row, dict) else None
        if not isinstance(got, dict) or not any(got.get(k) for k in keys):
            continue
        mine = by_source.setdefault(str(row.get("source_id") or ""), {k: 0 for k in keys})
        for k in keys:
            value = int(got.get(k) or 0)
            mine[k] += value
            total[k] += value
    return {**total, "by_source": dict(sorted(by_source.items()))}


def render(result: dict, max_named: int = 12) -> str:
    """One-screen summary of a gate result."""
    sources = ", ".join(result.get("sources") or []) or "(none)"
    head = (f"gate {result['preset']} [{sources}]: {result['files']} file(s), "
            + ("PASSED" if result["ok"] else f"FAILED ({len(result['problems'])} problem(s))"))
    lines = [head]
    code = result.get("gate_code") or {}
    if code.get("from") == "run":
        lines.append(f"  gate code: the run's own copy ({code.get('dir')}), verify_locate.py "
                     + (f"{str(code['sha256'])[:12]}, {code.get('files')} file(s) as planned"
                        if code.get("sha256") else "not checked"))
    elif code.get("from") == "checkout":
        lines.append(f"  gate code: this checkout's, verify_locate.py "
                     f"{str(code.get('sha256') or '')[:12]}")
    skipped = result.get("skipped") or {}
    if skipped.get("skips"):
        lines.append(f"  skips: {skipped['skips']} cover {skipped['chars']} character(s), "
                     f"{skipped['math_paragraphs']} math paragraph(s) and "
                     f"{skipped['finder_passages']} finder passage(s) that no passage keeps")
    cut = result.get("cut") or {}
    if cut:
        lines.append(f"  cut: {cut['formal']} formal passage(s) of {cut['passages']}, "
                     f"{cut['compound']} compound of {cut.get('conjuncts', 0)} conjunct(s); "
                     f"{cut['sentences']} sentence(s) and "
                     f"{cut['displays']} display(s), {cut['multi_sentence']} passage(s) of "
                     f"several sentences and {cut['multi_display']} of several displays")
    for problem in result["problems"][:max_named]:
        lines.append("  - " + problem)
    if len(result["problems"]) > max_named:
        lines.append(f"  ... +{len(result['problems']) - max_named} more")
    if result.get("hint"):
        lines.append("  hint: " + result["hint"])
    for note in result.get("partial_quotes", [])[:max_named]:
        lines.append("  ~ " + note)
    binding = result.get("binding_notes") or []
    for note in binding[:max_named]:
        lines.append("  ? " + note)
    if len(binding) > max_named:
        lines.append(f"  ? ... +{len(binding) - max_named} more")
    return "\n".join(lines)
