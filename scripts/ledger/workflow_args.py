"""Build the `args` objects the adjudication workflows take.

Ported from: recovered/c4082627_L225_Bash.txt, the hand-written
``workflow_inputs.json`` builder for the adam adjudication run (it joined
findings.adam.jsonl with declaration-snippets.json, target-refinement.json and
the three statement tables, and wrote a scratchpad path into the workflow).

Generalised here:

* nothing points at a session scratchpad -- every path is a campaign path;
* the finding rows carry their stable ``finding_id`` so the workflow's result
  links back to the corpus without matching on a concept prefix;
* declaration bodies come from ``companions/<name>/statements.jsonl`` (any
  adapter), not from one repo's overlay artifacts;
* peer rows are included for context only, capped, and clearly labelled.

The output is written by ``cf adjudicate --emit-args`` and passed verbatim as
the Workflow tool's ``args``.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from ledger import compat

#: peer rows per companion attached to a finding (the original capped at 12)
MAX_PEER_ROWS = 12
#: characters of a Lean signature/body carried into a prompt
MAX_SNIPPET = 1600


def _trim(text: Any, limit: int = MAX_SNIPPET) -> str:
    s = str(text or "")
    return s if len(s) <= limit else s[: limit - 3] + "..."


def load_statements(paths: Any, name: str) -> dict:
    """Index one companion's statements.jsonl by declaration and by item."""
    path = paths.statements(name)
    by_decl: dict[str, dict] = {}
    by_item: dict[str, list[dict]] = {}
    if not os.path.exists(path):
        return {"by_decl": by_decl, "by_item": by_item, "path": path, "rows": 0}
    rows = compat.data_rows(compat.read_jsonl(path))
    for row in rows:
        decl = compat.normalise_decl(row.get("decl") or row.get("name"))
        if decl:
            by_decl.setdefault(decl, row)
            by_decl.setdefault("%s:%s" % (name, decl), row)
        item = compat.normalise_item(row.get("item"))
        if item:
            by_item.setdefault(item, []).append(row)
    return {"by_decl": by_decl, "by_item": by_item, "path": path, "rows": len(rows)}


def _decl_block(index: dict, decl: str) -> dict:
    """The declaration context a lens agent needs: file, line, signature, body."""
    bare = decl.split(":", 1)[1] if ":" in decl else decl
    row = index["by_decl"].get(decl) or index["by_decl"].get(bare) or {}
    return {
        "name": bare,
        "file": row.get("file"),
        "line": row.get("line"),
        "statement": _trim(row.get("statement")),
        "body": _trim(row.get("body")) if row.get("body") else "",
        "sorry": row.get("sorry"),
        "page_anchor": row.get("page_anchor"),
        "found_in_statements": bool(row),
    }


def _peer_rows(indexes: dict[str, dict], item: str, exclude: str, cap: int) -> dict:
    out: dict[str, list[dict]] = {}
    for name, index in indexes.items():
        if name == exclude:
            continue
        rows = index["by_item"].get(compat.normalise_item(item)) or []
        if not rows:
            continue
        out[name] = [
            {
                "decl": r.get("decl") or r.get("name"),
                "file": r.get("file"),
                "line": r.get("line"),
                "statement": _trim(r.get("statement"), 900),
                "sorry": r.get("sorry"),
            }
            for r in rows[:cap]
        ]
    return out


def campaign_block(cfg: Any) -> dict:
    """The campaign paths every workflow prompt quotes (no scratchpad paths)."""
    paths = cfg.paths
    return {
        "dir": paths.root,
        "book_id": cfg.book_id,
        "book_title": cfg.title,
        "book_sha": cfg.book_sha,
        "pdf_page_offset": cfg.offset,
        "book_pages_dir": str(getattr(paths, "pages_dir", "")),
        "book_layout_txt": str(getattr(paths, "book_txt", "")),
        "items_json": str(getattr(paths, "items_json", "")),
        "errata": str(getattr(paths, "errata_md", "")),
        "errata_url": cfg.errata_url,
    }


def companion_block(cfg: Any, name: str) -> dict:
    """Repo, conventions text and statement table for the companion under audit."""
    paths = cfg.paths
    state = compat.read_state(paths, name)
    entry = cfg.companion(name)
    conventions_path = paths.conventions(name)
    conventions = ""
    if os.path.exists(conventions_path):
        with open(conventions_path, "r", encoding="utf-8") as fh:
            conventions = fh.read().strip()
    return {
        "name": name,
        "repo": str(entry.get("repo") or state.get("repo") or ""),
        "commit": str(state.get("commit") or ""),
        "owner": str(entry.get("owner") or ""),
        "write_policy": str(entry.get("write_policy") or "read_only"),
        "anchor": str(entry.get("anchor") or ""),
        "statements": paths.statements(name),
        "conventions": conventions,
        "conventions_file": conventions_path,
    }


def peer_blocks(cfg: Any, exclude: str) -> list[dict]:
    paths = cfg.paths
    out = []
    for name in cfg.companion_names():
        if name == exclude:
            continue
        state = compat.read_state(paths, name)
        entry = cfg.companion(name)
        out.append(
            {
                "name": name,
                "repo": str(entry.get("repo") or state.get("repo") or ""),
                "statements": paths.statements(name),
            }
        )
    return out


def finding_blocks(
    cfg: Any,
    companion: str,
    findings: Iterable[dict],
    *,
    max_peer_rows: int = MAX_PEER_ROWS,
    with_peers: bool = True,
) -> list[dict]:
    """Attach declaration bodies and peer rows to each routed finding."""
    paths = cfg.paths
    own = load_statements(paths, companion)
    indexes = {}
    if with_peers:
        for name in cfg.companion_names():
            if name != companion:
                indexes[name] = load_statements(paths, name)
    out = []
    for f in findings:
        decls = [
            d for d in (f.get("unfaithful_decls") or [])
            if not compat.decl_repo(d) or compat.decl_repo(d) == companion
        ]
        block = {
            "finding_id": f.get("finding_id", ""),
            "run_id": f.get("run_id", ""),
            "item": f.get("item", ""),
            "kind": f.get("kind", ""),
            "severity": f.get("severity", ""),
            "concept": _trim(f.get("concept"), 1200),
            "faithful": f.get("faithful", []),
            "unfaithful_decls": decls,
            "other_decls": f.get("other_decls", []),
            "book_quote": f.get("book_quote", ""),
            "reasoning": _trim(f.get("reasoning"), 2400),
            "suggested_fix": _trim(f.get("suggested_fix"), 1200),
            "decls": [_decl_block(own, d) for d in decls],
        }
        if f.get("witness"):
            block["witness"] = f["witness"]
        if f.get("bridge") is not None:
            block["bridge"] = f["bridge"]
        if indexes:
            peers = _peer_rows(indexes, f.get("item", ""), companion, max_peer_rows)
            if peers:
                block["peer_rows"] = peers
        out.append(block)
    return out


def build_adjudicate_args(
    cfg: Any,
    companion: str,
    findings: Iterable[dict],
    *,
    definitions: Iterable[dict] = (),
    uncovered: Iterable[dict] = (),
    assessments: Iterable[dict] = (),
    contested_threshold: float = 0.7,
    notes: str = "",
    model: str = "opus",
) -> dict:
    """args for workflows/adjudicate.workflow.js."""
    return {
        "workflow": "adjudicate",
        "campaign": campaign_block(cfg),
        "companion": companion_block(cfg, companion),
        "peers": peer_blocks(cfg, companion),
        "findings": finding_blocks(cfg, companion, findings),
        "definitions": list(definitions),
        "uncovered_definitions": list(uncovered),
        "assessments": list(assessments),
        "contested_threshold": contested_threshold,
        "notes": notes,
        "model": model,
    }


def build_challenge_args(
    cfg: Any,
    companion: str,
    findings: Iterable[dict],
    *,
    refuters: int = 2,
    notes: str = "",
    model: str = "opus",
) -> dict:
    """args for workflows/challenge.workflow.js."""
    return {
        "workflow": "challenge",
        "campaign": campaign_block(cfg),
        "companion": companion_block(cfg, companion),
        "peers": peer_blocks(cfg, companion),
        "findings": finding_blocks(cfg, companion, findings),
        "refuters": refuters,
        "notes": notes,
        "model": model,
    }


def agent_estimate(args_obj: dict) -> int:
    """How many subagents the workflow will spawn (session-limit planning).

    adjudicate: 3 lenses + 1 reconciler + 2 skeptics per finding, 2 readers + 1
    reconciler per definition, 1 per uncovered definition and assessment, + critic.
    challenge: ``refuters`` per finding.
    """
    if args_obj.get("workflow") == "challenge":
        return len(args_obj.get("findings") or []) * int(args_obj.get("refuters") or 2)
    return (
        len(args_obj.get("findings") or []) * 6
        + len(args_obj.get("definitions") or []) * 3
        + len(args_obj.get("uncovered_definitions") or [])
        + len(args_obj.get("assessments") or [])
        + 1
    )
