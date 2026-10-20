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
``locate.gate_files`` and then loaded apart from the checkout's modules
(:func:`gate_locate_corpus`), so a gate edited or upgraded since the plan never
judges the corpus its workers were judged by; the receipt's ``gate_code`` names
that gate by hash (references/locate-documents.md section 10).
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import sys
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


#: what a locate gate directory imports by its own top-level names (verify_locate.py,
#: its siblings and the vendored packages): swapped out of ``sys.modules`` while a
#: run's own gate copy is loaded, so it runs on its own files, never the checkout's
_GATE_MODULES = ("verify_locate", "locate_text", "verify_findings", "locate_records")
_GATE_PACKAGES = ("literature_links", "formal_companion_annotations")
#: the staged files of a run's gate directory that are no gate code: the host config,
#: whose local twin is gate.local.json, and the task manifests (not in gate_files)
_NOT_GATE_CODE = ("gate/gate.json",)
#: what Python can import from a directory on its path
_IMPORTABLE = frozenset({".py", ".pyc", ".pyo", ".pyd", ".so", ".pth"})


class GateLoadError(Exception):
    """A run's own gate copy that does not import: the gate could not run."""


def _is_gate_module(name: str) -> bool:
    return name in _GATE_MODULES or name.split(".")[0] in _GATE_PACKAGES


@contextlib.contextmanager
def _isolated_gate(gate_dir: Path):
    """Import ``<gate_dir>/verify_locate.py`` with its siblings and vendored packages.

    A gate directory is self-contained (references/locate-documents.md section 9:
    every module it imports is the standard library or a file of the directory),
    so the copy runs as it ran on the host: the checkout's modules of the same
    names are set aside for the duration and put back after, with ``sys.path``.
    """
    saved = {name: module for name, module in sys.modules.items() if _is_gate_module(name)}
    for name in saved:
        del sys.modules[name]
    saved_path = list(sys.path)
    saved_bytecode = sys.dont_write_bytecode
    sys.path[:0] = [str(gate_dir), str(gate_dir / "vendor")]
    # the run's directory is the plan's record: an import leaves no __pycache__ in it
    sys.dont_write_bytecode = True
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
        for name in [n for n in sys.modules if _is_gate_module(n)]:
            del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = saved_path
        sys.dont_write_bytecode = saved_bytecode


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
    # other stray file (a desktop's .DS_Store) imports nothing and is let be.
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


def checkout_gate_code() -> dict:
    """The ``gate_code`` block of a re-gate by this checkout's gate (``--checkout-gate``)."""
    script = _SCRIPTS / "gate" / "verify_locate.py"
    return {"from": "checkout", "dir": str(script.parent),
            "sha256": hashing.sha256_file(script)}


def gate_locate_corpus(dest: str | Path, gate_config: str | Path, *,
                       gate_dir: str | Path | None = None,
                       gate_files: dict | None = None) -> dict:
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
    (``gate_code.from: checkout``).
    """
    cfg_path = Path(gate_config).expanduser()
    if not cfg_path.exists():
        raise CfError(f"no gate config at {cfg_path}",
                      hint="the locate planner writes runs/<id>/gate.local.json; pass --gate-config")
    if gate_dir is None:
        from gate import verify_locate as vl

        return _gate_locate_with(vl, dest, cfg_path, checkout_gate_code())
    gate_dir = Path(gate_dir)
    block, problems = run_gate_code(gate_dir, gate_files)
    if problems:
        return {"ok": False, "config": str(cfg_path), "preset": "locate", "sources": [],
                "files": 0, "problems": [f"the gate could not run: {p}" for p in problems],
                "partial_quotes": [], "binding_notes": [], "unbound_quotes": 0,
                "broken": problems, "gate_code": block}
    try:
        with _isolated_gate(gate_dir) as vl:
            return _gate_locate_with(vl, dest, cfg_path, block)
    except GateLoadError as exc:
        why = str(exc)
        return {"ok": False, "config": str(cfg_path), "preset": "locate", "sources": [],
                "files": 0, "problems": [f"the gate could not run: {why}"], "partial_quotes": [],
                "binding_notes": [], "unbound_quotes": 0, "broken": [why], "gate_code": block}


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
    return out


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
    for problem in result["problems"][:max_named]:
        lines.append("  - " + problem)
    if len(result["problems"]) > max_named:
        lines.append(f"  ... +{len(result['problems']) - max_named} more")
    for note in result.get("partial_quotes", [])[:max_named]:
        lines.append("  ~ " + note)
    binding = result.get("binding_notes") or []
    for note in binding[:max_named]:
        lines.append("  ? " + note)
    if len(binding) > max_named:
        lines.append(f"  ? ... +{len(binding) - max_named} more")
    return "\n".join(lines)
