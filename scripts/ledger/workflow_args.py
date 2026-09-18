"""Build the `args` objects the adjudication workflows take.

Ported from: recovered/c4082627_L225_Bash.txt, the hand-written
``workflow_inputs.json`` builder for the adam adjudication run (it joined
findings.adam.jsonl with declaration-snippets.json, target-refinement.json and
the three statement tables, and wrote a scratchpad path into the workflow).

Generalised here:

* nothing points at a session scratchpad -- every path is a workspace path;
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
        item = compat.item_join_key(row.get("item"))
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
        rows = index["by_item"].get(compat.item_join_key(item)) or []
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


def source_block(cfg: Any, source_id: str) -> dict:
    """One source's paths and identity, as a workflow prompt quotes them."""
    paths = cfg.paths
    src = cfg.source(source_id)
    return {
        "source_id": src.id,
        "title": src.title or src.id,
        "edition": src.edition,
        "source_sha": src.sha256,
        "page_offset": src.offset,
        "pages_dir": str(paths.source_pages_dir(src.id)),
        "text": str(paths.source_text(src.id)),
        "items_json": str(paths.source_items(src.id)),
        "errata": str(paths.source_errata_md(src.id)),
        "errata_url": src.errata_url,
    }


def workspace_block(cfg: Any, source_ids: Iterable[str] = ()) -> dict:
    """The workspace paths every workflow prompt quotes (no scratchpad paths).

    ``sources`` is a LIST: a companion is audited against a list of sources, and
    an adjudicator told only about "the book" cannot know which of two editions
    a finding read.  Each finding row carries its own ``source_id``, and this
    block is what turns that id into a path the agent can actually open.
    """
    paths = cfg.paths
    ids = [str(s) for s in source_ids] or cfg.source_ids()
    return {
        "dir": paths.root,
        "name": cfg.title,
        "sources": [source_block(cfg, sid) for sid in ids],
    }


def companion_block(cfg: Any, name: str) -> dict:
    """Repo, conventions text and statement table for the companion under audit."""
    paths = cfg.paths
    state = compat.read_state(paths, name)
    entry = cfg.companion(name)
    sources = cfg.sources_of(name) or cfg.source_ids()
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
        # The sources this companion is audited against, and the one whose
        # PAGINATION its page anchors follow -- which is not always one it
        # ships (tao-measure-lean bundles the 2011 draft and anchors to the
        # published book).
        "sources": list(sources),
        "anchor_source": cfg.anchor_source(name),
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
            "source_id": f.get("source_id", ""),
            "source_sha": f.get("source_sha", ""),
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


def _sources_in(cfg: Any, companion: str, findings: Iterable[dict]) -> list:
    """Which sources a workflow must be told about.

    Every source the findings actually name, plus the companion's own list, so
    an agent can always open the text a row quotes.  ``findings`` may be a
    generator in principle, so it is materialised by the callers before this is
    reached.
    """
    named = {str(f.get("source_id") or "") for f in findings}
    named.discard("")
    out = list(cfg.sources_of(companion))
    for sid in sorted(named):
        if sid not in out:
            out.append(sid)
    return out or cfg.source_ids()


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
    source_ids: Iterable[str] = (),
) -> dict:
    """args for workflows/adjudicate.workflow.js."""
    findings = list(findings)
    ids = [str(s) for s in source_ids] or _sources_in(cfg, companion, findings)
    return {
        "workflow": "adjudicate",
        "workspace": workspace_block(cfg, ids),
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
    source_ids: Iterable[str] = (),
) -> dict:
    """args for workflows/challenge.workflow.js."""
    findings = list(findings)
    ids = [str(s) for s in source_ids] or _sources_in(cfg, companion, findings)
    return {
        "workflow": "challenge",
        "workspace": workspace_block(cfg, ids),
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
