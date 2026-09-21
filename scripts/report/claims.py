"""Build the args for workflows/verify-claims.workflow.js.

Ported from: the hand-written lens list and source list of the v1 campaign's
verify-audit-summary workflow (not in this repo), which hard-coded five audit
corpora, two repo checkouts, six note files and a PR list.  Here the same lenses
are generated from the workspace's own paths, and the numbers lens is generated
from the verification commands the handoff already prints -- so the verifier
re-derives figures with the commands the report claims produce them.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from ledger import compat


def _exists(path: str) -> bool:
    return bool(path) and os.path.exists(path)


def build_sources(workspace: str, companions: Iterable[str], cfg: Any) -> list[dict]:
    """The primary artifacts a verifier is allowed to re-derive claims from."""
    paths = cfg.paths
    out: list[dict] = []
    corpus = paths.corpus()
    if _exists(corpus):
        runs = sorted(
            d for d in os.listdir(corpus) if os.path.isdir(os.path.join(corpus, d))
        )
        out.append(
            {
                "label": "corpus (one JSON per book item per run; rows live in `disagreements`)",
                "path": corpus,
                "note": "runs: %s" % (", ".join(runs) or "none yet"),
            }
        )
    if _exists(paths.reports):
        out.append({"label": "reports (tally.<run>.json, handoffs)", "path": paths.reports, "note": ""})
    if _exists(paths.ledger):
        out.append(
            {
                "label": "ledger (accepted/refuted rows, adjudicated.<name>.md)",
                "path": paths.ledger,
                "note": "every row links to a finding by finding_id",
            }
        )
    if _exists(paths.routed):
        out.append(
            {
                "label": "routed findings with a `_meta` status row",
                "path": paths.routed,
                "note": "status UNCHALLENGED means nothing may be reported upstream",
            }
        )
    for name in companions:
        st = compat.read_state(paths, name)
        if st.get("repo"):
            out.append(
                {
                    "label": "companion `%s` (read-only)" % name,
                    "path": str(st["repo"]),
                    "note": "commit %s" % str(st.get("commit", ""))[:12],
                }
            )
        if _exists(paths.statements(name)):
            out.append(
                {"label": "statement table for `%s`" % name, "path": paths.statements(name), "note": ""}
            )
        if _exists(paths.state(name)):
            out.append({"label": "state.json for `%s`" % name, "path": paths.state(name), "note": ""})
    # One entry per SOURCE. A workspace can hold two editions of one work, and a
    # verifier told about "the book" would check a quote against whichever it
    # opened first.
    for source_id in cfg.source_ids():
        src = cfg.source(source_id)
        sdir = str(paths.source_dir(source_id))
        if not _exists(sdir):
            continue
        out.append(
            {
                "label": "source `%s` -- text and page map "
                         "(gitignored, never leaves the workspace dir)" % source_id,
                "path": sdir,
                "note": "sha256 %s; printed page = PDF page - %d%s"
                        % (src.sha256 or "(unknown)", src.offset,
                           "; errata %s" % src.errata_url if src.errata_url else ""),
            }
        )
    return out


def default_lenses(workspace: str, companions: Iterable[str], cfg: Any = None) -> list[dict]:
    """The claim lenses, generalised from the six that found 19 wrong claims."""
    cfg = cfg if cfg is not None else compat.load_workspace(workspace)
    paths = cfg.paths
    names = ", ".join("`%s`" % n for n in companions) or "(none)"
    source_names = ", ".join("`%s`" % s for s in cfg.source_ids()) or "(none)"
    return [
        {
            "key": "corpus-numbers",
            "prompt": (
                "CORPUS NUMBERS. Every number in the draft that is derivable from the corpus: finding files per "
                "run, rows per companion, distinct items per companion, the partition of those items by kind "
                "(a row's kind is CONTENT/VACUITY/PROXY/SUSPECTED_ERRATA/COVERAGE/PAGE_ANCHOR), and per-run "
                "counts. Write and run your own Python over %s; paste the script and its output as evidence. "
                "Check the corpus is complete first (RECEIPT.json count == file count) and say so."
                % paths.corpus()
            ),
        },
        {
            "key": "ledger-and-routing",
            "prompt": (
                "LEDGER AND ROUTING. For each companion (%s): the number of accepted and refuted rows, the "
                "verdict partition, how many rows are contested, and the `_meta.status` of the routed file. "
                "Verify the routing rule actually used ('row names a declaration of the repo in "
                "unfaithful_decls') by re-running it yourself over the corpus and comparing your count with "
                "the routed file's `_meta.counts`. Verify that every ledger row's finding_id appears in the "
                "corpus, that CONVENTION rows carry a recovery argument, and that every row's source_id is "
                "one of %s with the source_sha that workspace.yaml pins for it. If the draft presents any "
                "finding as confirmed while its routed status is UNCHALLENGED, that claim is WRONG."
                % (names, source_names)
            ),
        },
        {
            "key": "method-and-scope",
            "prompt": (
                "METHOD AND SCOPE. Verify every claim about HOW the audit ran: the number of lenses, skeptics "
                "and refuters per finding; which gates ran and where they ran from; which runs used which "
                "model and whether the book was read as text or as page images; the claim (if made) that no "
                "finding rests on a fabricated quote. Check these against run.yaml, the workflow scripts in "
                "the skill's workflows/ directory and the ledger's `method` fields, not against prose. Also "
                "check the scope disclaimer: this version of the pipeline produces FINDINGS ONLY -- if the "
                "draft claims a fix, a PR review or a camera-ready audit as work this pipeline did, it is WRONG. "
                "Also check every claim about WHICH SOURCE was read: a run audits one source, and a number "
                "that pools two editions of one work is WRONG even when both editions were audited."
            ),
        },
        {
            "key": "claims-about-others",
            "prompt": (
                "CLAIMS ABOUT OTHER PEOPLE'S REPOSITORIES. List every sentence that asserts something about a "
                "companion repository (that it is unfaithful somewhere, that it is clean, that it stops at a "
                "section, that a defect was fixed). For each, check the evidence actually present in the "
                "workspace: a ledger row with a verdict, a routed row with status CHALLENGED, or a computed "
                "coverage figure. An adjudicated-but-unchallenged finding does not support a claim about "
                "someone else's repository. Mark anything stronger than its evidence WRONG and give the "
                "weaker wording the evidence does support."
            ),
        },
    ]


def numbers_block(workspace: str, verification_commands: str,
                  extra_checks: Iterable[str] = ()) -> dict:
    """The independent second vote on the numbers."""
    commands = [
        line.strip()
        for line in (verification_commands or "").splitlines()
        if line.strip() and not line.strip().startswith(("#", "```"))
    ]
    checks = list(extra_checks) or [
        "every count and every denominator in the draft",
        "that any 'nothing is open' claim ships with the command that recomputes it",
        "that counts taken over the corpus name the runs they cover",
        "that every source_id and source_sha in the draft is one workspace.yaml pins",
    ]
    return {"enabled": True, "commands": commands, "checks": checks}


def build_args(
    workspace: str,
    *,
    draft: str,
    companions: Iterable[str],
    workspace_cfg: Any = None,
    verification_commands: str = "",
    appendix: str = "",
    lenses: Iterable[dict] | None = None,
) -> dict:
    """Assemble the full args object for verify-claims.workflow.js."""
    companions = list(companions)
    cfg = workspace_cfg if workspace_cfg is not None else compat.load_workspace(workspace)
    return {
        "draft": draft,
        "sources": build_sources(workspace, companions, cfg),
        "lenses": (list(lenses) if lenses is not None
                   else default_lenses(workspace, companions, cfg)),
        "numbers": numbers_block(workspace, verification_commands),
        "extra": {"appendix": appendix} if appendix else {},
        "model": "opus",
    }
