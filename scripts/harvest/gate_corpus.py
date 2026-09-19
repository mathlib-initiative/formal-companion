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
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - import side effect
    sys.path.insert(0, str(_SCRIPTS))

from gate import verify_findings as vf  # noqa: E402

from harvest._compat import CfError, NON_FINDING_FILES  # noqa: E402

#: A harvested corpus keeps its findings directly in ``corpus/<run-id>/``, not in
#: a ``findings/`` subdirectory the way a worker's worktree does.
CORPUS_FINDINGS_DIR = "."


def gate_corpus(dest: str | Path, gate_config: str | Path,
                findings_dir: str = CORPUS_FINDINGS_DIR) -> dict:
    """Validate every findings file in *dest* against *gate_config*.

    Returns ``{ok, config, preset, files, problems, partial}``; ``problems`` is
    the same list ``verify_findings.main`` would print, so the receipt records
    exactly what the gate said.
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

    files = [f for f in sorted((dest / cfg.findings_dir).glob("*.json"))
             if f.name not in NON_FINDING_FILES and not f.name.startswith(".")]
    partial: list[str] = []
    notes: list[str] = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - any parse failure is a gate failure
            problems.append(f"{f.name}: not valid JSON ({exc})")
            continue
        problems.extend(vf.validate_doc(doc, f.name, cfg, books, decl_index))
        book = vf._book_for(books, vf.doc_source_id(doc, cfg))
        partial.extend(vf.partial_quotes(doc, f.name, book, max_chars=cfg.max_quote_chars,
                                         max_pages=cfg.max_match_pages))
        notes.extend(vf.binding_notes(doc, f.name, cfg, book))
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


def render(result: dict, max_named: int = 12) -> str:
    """One-screen summary of a gate result."""
    sources = ", ".join(result.get("sources") or []) or "(none)"
    head = (f"gate {result['preset']} [{sources}]: {result['files']} file(s), "
            + ("PASSED" if result["ok"] else f"FAILED ({len(result['problems'])} problem(s))"))
    lines = [head]
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
