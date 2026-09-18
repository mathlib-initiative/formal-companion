"""Stage 9 -- route findings to the repository that owns them.

Ported from: recovered/b174aa13_L5702_Bash.txt (the routing cell that wrote
/Users/jack/Desktop/tao-differential/findings.{adam,ours}.jsonl), with three
changes the record forces:

1.  The fallback branch is GONE.  The original ``side()`` also admitted a row
    when the repo merely appeared in ``repos_involved`` and was not the
    ``faithful`` one::

        def side(f, repo):
            return bool(names(f, repo)) or (f['faithful'] not in (repo,'unsure','none')
                                            and repo in (f.get('repos_involved') or []))

    That second disjunct is what produced the "31 findings" scare for a repo
    that was named in none of them.  Routing is now exactly: the row names a
    declaration of that repo in ``unfaithful_decls``.

2.  ``_meta`` is structured (status / rule / sources / counts) instead of two
    prose sentences, because ``route`` now REFUSES to emit anything upstream
    while the status is UNCHALLENGED -- the 32 held-back teorth findings and the
    ~40% first-pass false-positive rate are the reason the challenge stage
    exists.

3.  PAGE_ANCHOR is mechanical-only in vocabulary v2.  An LLM-emitted anchor row
    is withdrawn here with the command that re-derives it
    (``pair/anchor_check.py`` against the pinned edition); anchor rows produced
    by that check are routed as ordinary low-severity mechanical rows, and only
    rows the pinned edition contradicts are withdrawn.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Iterable

from ledger import compat
from ledger import findings as findings_mod
from ledger import validate_ledger as vl

#: the one routing rule
ROUTING_RULE = "row names a declaration of the repo in unfaithful_decls"
#: The ONE provenance value/key for a mechanical anchor row.  ``pair/anchor_check.py``
#: writes ``source: "anchor_check"``, the gate reads ``g["source"]`` and this module
#: reads the same key; the fact used to be spelled three ways (``mechanical: True``,
#: ``source``, ``provenance``) and nothing matched anything.
MECHANICAL_SOURCE = "anchor_check"
#: legacy spelling, still read so an older corpus routes
_PROVENANCE_KEYS = ("source", "provenance")

STATUS_CHALLENGED = compat.STATUS_CHALLENGED
STATUS_UNCHALLENGED = compat.STATUS_UNCHALLENGED


class UpstreamRefused(RuntimeError):
    """Raised when an upstream export is attempted on an UNCHALLENGED file."""


def challenge_command(workspace: str, companion: str) -> str:
    """The exact command that turns UNCHALLENGED into CHALLENGED."""
    return (
        "python3 scripts/cf.py adjudicate --workspace %s --companion %s "
        "--challenge-only --emit-args" % (workspace, companion)
    )


def _is_payload(value: Any) -> bool:
    """True when *value* looks like one anchor-check document, not a map of them."""
    if isinstance(value, list):
        return True
    return isinstance(value, dict) and any(
        k in value for k in ("rows", "items", "anchors", "summary", "checked_items"))


def _by_source(anchor_check: Any) -> dict[str, Any]:
    """Normalise the ``anchor_check`` argument into ``{source_id: payload}``.

    ``route_workspace`` passes a map keyed by ``source_id`` -- the oracle is per
    source, because ``pairs/<source_id>/anchor-check.json`` only ever examined
    ITS edition's items.  A single payload (what a direct caller or a test
    passes) is kept under ``""`` and used for every source, which preserves the
    one-source behaviour exactly.
    """
    if not anchor_check:
        return {}
    if _is_payload(anchor_check):
        return {"": anchor_check}
    return {str(k): v for k, v in dict(anchor_check).items()}


def _anchor_index(anchor_check: Any) -> dict[str, dict]:
    """Index a pairs/<source_id>/anchor-check.json payload by normalised item."""
    if not anchor_check:
        return {}
    rows = anchor_check
    if isinstance(anchor_check, dict):
        rows = (
            anchor_check.get("rows")
            or anchor_check.get("items")
            or anchor_check.get("anchors")
            or []
        )
    out: dict[str, dict] = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("item"):
            out[compat.item_join_key(row["item"])] = row
    return out


def provenance(row: dict) -> str:
    """The row's provenance, whichever of the historical keys carries it."""
    for key in _PROVENANCE_KEYS:
        value = row.get(key)
        if value:
            return str(value)
    return ""


def _withdrawal(row: dict, anchors: dict[str, dict], checked: set[str],
                source_id: str = "") -> str:
    """Return a withdrawal reason for *row*, or '' when it should be routed.

    critique.json correction 2: "Route them as mechanical low-severity rows from
    ``anchor_check.py`` against the pinned edition; **withdraw only rows
    contradicted by that edition**."  Three cases, all reachable:

    * the row IS an ``anchor_check`` row -- it is the oracle, so it routes;
    * an LLM row whose item the mechanical check ALSO found mismatched -- the
      edition confirms it, so it routes (at the mechanical row's low severity);
    * an LLM row whose item the check examined and found correct -- contradicted,
      withdrawn.  An item the check never examined cannot be confirmed either
      way, so it is withdrawn with the re-derive instruction instead.

    The old version tested ``anchor.get("agrees") is True``, a key
    ``anchor_check.check()`` never writes (it emits mismatches only), so both
    branches were dead and every non-``anchor_check`` row was withdrawn wholesale.
    """
    kind = str(row.get("kind") or "")
    if kind not in compat.MECHANICAL_KINDS:
        return ""
    if provenance(row) == MECHANICAL_SOURCE:
        return ""
    item = compat.item_join_key(row.get("item"))
    if item in anchors:
        return ""  # the mechanical check found the same mismatch: route it
    where = "pairs/%s/anchor-check.json" % source_id if source_id else "pairs/<source>/anchor-check.json"
    if item in checked:
        return (
            "contradicted by the pinned edition: anchor_check examined %s and the "
            "repo anchor is correct (%s)" % (row.get("item") or item, where)
        )
    return (
        "%s is mechanical-only in vocabulary v2 and anchor_check never examined "
        "this item for %s; re-derive with `python3 scripts/cf.py pair`"
        % (kind, source_id or "this source")
    )


def _checked_items(anchor_check: Any) -> set[str]:
    """Items ``anchor_check`` actually examined (``summary.checked_items``)."""
    if not isinstance(anchor_check, dict):
        return set()
    summary = anchor_check.get("summary")
    names = (summary or {}).get("checked_items") if isinstance(summary, dict) else None
    if names is None:
        names = anchor_check.get("checked_items")
    return {compat.item_join_key(n) for n in (names or [])}


def route_repo(
    findings: Iterable[dict],
    companion: str,
    *,
    source_id: str = "",
    source_sha: str = "",
    ledger_rows: Iterable[dict] | None = None,
    anchor_check: Any = None,
    workspace: str = "",
) -> dict:
    """Route every finding that names *companion* and decide the file's status.

    ``source_id``/``source_sha`` are the fallbacks for a finding that carries
    none; a row's own source always wins, and the meta records every source the
    routed file touches.  A routed file may span sources (one companion audited
    against two books), so the edition is a per-row fact, not a file-level one.

    Returns ``{"rows": [...], "meta": {...}}``; the caller writes it out.
    """
    rows_in = list(findings)
    mine = findings_mod.rows_naming(rows_in, companion)
    payloads = _by_source(anchor_check)
    anchors = {sid: _anchor_index(p) for sid, p in payloads.items()}
    checked = {sid: _checked_items(p) for sid, p in payloads.items()}

    def _oracle(sid: str) -> tuple:
        """The (mismatches, examined) oracle for one source.

        Falls back to the unkeyed payload -- and ONLY to that -- so a
        single-payload caller behaves as before while a per-source map never
        judges one edition's item by another edition's check.
        """
        if sid in anchors:
            return anchors[sid], checked[sid]
        if "" in anchors:
            return anchors[""], checked[""]
        return {}, set()

    ledger_by_id: dict[str, dict] = {}
    for row in ledger_rows or []:
        ledger_by_id.setdefault(str(row.get("finding_id") or ""), row)

    routed: list[dict] = []
    for f in sorted(
        mine,
        key=lambda r: (
            {"high": 0, "medium": 1, "low": 2}.get(str(r.get("severity")), 3),
            r.get("item", ""),
            r.get("run_id", ""),
        ),
    ):
        row = {
            "finding_id": f["finding_id"],
            "run_id": f.get("run_id", ""),
            "source_id": f.get("source_id", "") or source_id,
            "source_sha": f.get("source_sha", "") or source_sha,
            "item": f["item"],
            "kind": f.get("kind", ""),
            "severity": f.get("severity", ""),
            "repo": companion,
            "unfaithful_decls": findings_mod.decls_of(f, companion),
            "other_decls": [
                d for d in findings_mod.decls_of(f) if compat.decl_repo(d) != companion
            ],
            "faithful": f.get("faithful", []),
            "concept": f.get("concept", ""),
            "book_quote": f.get("book_quote", ""),
            "reasoning": f.get("reasoning", ""),
            "suggested_fix": f.get("suggested_fix", ""),
            "provenance": f.get("provenance", ""),
            "source_file": f.get("source_file", ""),
        }
        if f.get("witness"):
            row["witness"] = f["witness"]
        if f.get("bridge") is not None:
            row["bridge"] = f["bridge"]
        row_source = str(row.get("source_id") or "")
        row_anchors, row_checked = _oracle(row_source)
        reason = _withdrawal(f, row_anchors, row_checked, row_source)
        if reason:
            row["withdrawn"] = True
            row["withdrawn_reason"] = reason
        ledger = ledger_by_id.get(f["finding_id"])
        if ledger is not None:
            row["challenged"] = True
            row["ledger_verdict"] = ledger.get("verdict", "")
            row["ledger_method"] = ledger.get("method", "")
            if ledger.get("contested"):
                row["contested"] = True
            if ledger.get("resolution"):
                row["resolution"] = ledger["resolution"]
        else:
            row["challenged"] = False
        routed.append(row)

    open_rows = [r for r in routed if not r.get("withdrawn")]
    unchallenged = [r for r in open_rows if not r.get("challenged")]
    status = STATUS_UNCHALLENGED if unchallenged else STATUS_CHALLENGED

    by_kind: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for r in open_rows:
        by_kind[str(r.get("kind") or "")] = by_kind.get(str(r.get("kind") or ""), 0) + 1
        by_severity[str(r.get("severity") or "")] = (
            by_severity.get(str(r.get("severity") or ""), 0) + 1
        )

    sources: dict[str, str] = {}
    for r in routed:
        sid = str(r.get("source_id") or "")
        if sid:
            sources.setdefault(sid, str(r.get("source_sha") or ""))
    if source_id and source_id not in sources:
        sources[source_id] = source_sha
    # Two shas under ONE source_id means two editions were routed as though they
    # were the same source: exactly the batch-1 defect, now nameable.
    conflicts = sorted({sid for sid in sources
                        if len({str(r.get("source_sha") or "") for r in routed
                                if str(r.get("source_id") or "") == sid
                                and r.get("source_sha")}) > 1})
    meta = {
        "repo": companion,
        "status": status,
        "rule": ROUTING_RULE,
        "sources": dict(sorted(sources.items())),
        "counts": {
            "rows": len(open_rows),
            "items": len({(r.get("source_id", ""), r["item"]) for r in open_rows}),
            "by_source": {
                sid: sum(1 for r in open_rows if str(r.get("source_id") or "") == sid)
                for sid in sorted(sources)
            },
            "withdrawn": len(routed) - len(open_rows),
            "challenged": len(open_rows) - len(unchallenged),
            "unchallenged": len(unchallenged),
            "by_kind": by_kind,
            "by_severity": by_severity,
            "runs": sorted({r.get("run_id", "") for r in open_rows if r.get("run_id")}),
        },
        "schema": "routed-v2",
    }
    if conflicts:
        meta["source_sha_conflict"] = conflicts
    if status == STATUS_UNCHALLENGED:
        meta["upstream"] = "REFUSED while UNCHALLENGED"
        meta["challenge_command"] = challenge_command(workspace or "<workspace>", companion)
    return {"rows": routed, "meta": meta}


def write_routed(paths: Any, companion: str, routed: dict) -> str:
    """Write ledger/routed/findings.<companion>.jsonl. Returns the path."""
    path = paths.routed_findings(companion)
    compat.write_jsonl(path, routed["rows"], meta=routed["meta"])
    return path


def read_routed(path: str) -> dict:
    """Read a routed file back into ``{"rows": [...], "meta": {...}}``."""
    raw = compat.read_jsonl(path)
    return {"rows": compat.data_rows(raw), "meta": compat.meta_row(raw)}


def _redact(row: dict) -> dict:
    """Replace the book quote with (length, hash) for anything shared upstream."""
    out = dict(row)
    quote = str(out.get("book_quote") or "")
    if quote:
        out["book_quote"] = {
            "redacted": True,
            "chars": len(quote),
            "sha256": compat.sha256_text(" ".join(quote.split()).lower()),
        }
    return out


#: verdicts that may be reported to a repository owner. UNSURE is challenged
#: but undecided, so it is held back with the rest: the human-confirmation
#: policy is that a machine verdict the pipeline cannot defend does not travel.
REPORTABLE_VERDICTS = frozenset({"CONFIRMED_CONTENT", "ATTRIBUTION_GAP"})


def emit_upstream(
    routed: dict,
    dest_dir: str,
    *,
    companion: str,
    workspace: str = "",
    redact_quotes: bool = False,
    open_only: bool = True,
) -> str:
    """Write the upstream report for a routed file.

    Refuses -- loudly -- while ``_meta.status`` is UNCHALLENGED: a first-pass
    finding is wrong about 40% of the time, and 32 unchallenged findings were
    held back from teorth for exactly this reason.  It also refuses a file in
    which one ``source_id`` carries more than one ``source_sha`` -- that means
    two editions were routed as if they were the same source, which is how
    batch 1's anchor findings ended up judged against the 2011 draft.  Rows
    from two DIFFERENT sources are fine: they say which is which.
    """
    meta = routed.get("meta") or {}
    if meta.get("status") != STATUS_CHALLENGED:
        raise UpstreamRefused(
            "refusing to emit an upstream report for %s: status is %s "
            "(%d unchallenged row(s)). Challenge them first:\n    %s"
            % (
                companion,
                meta.get("status", "UNKNOWN"),
                (meta.get("counts") or {}).get("unchallenged", 0),
                meta.get("challenge_command") or challenge_command(workspace or "<workspace>", companion),
            )
        )
    if meta.get("source_sha_conflict"):
        raise UpstreamRefused(
            "refusing to emit an upstream report for %s: source(s) %s carry rows judged "
            "against more than one edition. Re-run the stages that produced the odd one."
            % (companion, ", ".join(meta["source_sha_conflict"]))
        )
    rows = [r for r in routed["rows"] if not r.get("withdrawn")]
    held = []
    if open_only:
        held = [
            r for r in rows
            if str(r.get("ledger_verdict") or "") not in REPORTABLE_VERDICTS
            and str(r.get("ledger_verdict") or "") in compat.OPEN_VERDICTS
        ]
        rows = [r for r in rows if str(r.get("ledger_verdict") or "") in REPORTABLE_VERDICTS]
    if redact_quotes:
        rows = [_redact(r) for r in rows]
    out_meta = dict(meta)
    out_meta["export"] = "upstream"
    out_meta["redacted_quotes"] = bool(redact_quotes)
    out_meta["reportable_verdicts"] = sorted(REPORTABLE_VERDICTS)
    out_meta["counts"] = dict(meta.get("counts") or {}, exported=len(rows), held=len(held))
    if held:
        out_meta["held_back"] = [
            {"item": r.get("item"), "verdict": r.get("ledger_verdict")} for r in held
        ]
    path = os.path.join(dest_dir, "findings.%s.upstream.jsonl" % companion)
    compat.write_jsonl(path, rows, meta=out_meta)
    return path


def owner_message(routed: dict, companion: str, owner: str = "") -> str:
    """A DRAFT message for the repo owner. Nothing here sends anything."""
    meta = routed.get("meta") or {}
    counts = meta.get("counts") or {}
    rows = [r for r in routed["rows"] if not r.get("withdrawn")]
    lines = [
        "# DRAFT message to the owner of `%s`%s" % (companion, (" (%s)" % owner) if owner else ""),
        "",
        "> Not sent by any script. Read it, edit it, send it yourself.",
        "",
        "Status: **%s** — %d row(s) over %d item(s), %d challenged, %d unchallenged."
        % (
            meta.get("status", ""),
            counts.get("rows", 0),
            counts.get("items", 0),
            counts.get("challenged", 0),
            counts.get("unchallenged", 0),
        ),
        "Sources: %s. Routing rule: %s."
        % (", ".join("`%s` (%s…)" % (sid, str(sha)[:12])
                     for sid, sha in sorted((meta.get("sources") or {}).items())) or "(none)",
           meta.get("rule", "")),
        "",
    ]
    if meta.get("status") != STATUS_CHALLENGED:
        lines += [
            "**These rows have NOT been challenged and must not be sent as findings.**",
            "Run: `%s`" % (meta.get("challenge_command") or ""),
            "",
        ]
    multi = len(meta.get("sources") or {}) > 1
    header = "| item | kind | severity | declaration | verdict |"
    rule = "|---|---|---|---|---|"
    if multi:
        # Two sources: the item column alone is ambiguous, so name the source.
        header = "| source | item | kind | severity | declaration | verdict |"
        rule = "|---|---|---|---|---|---|"
    lines += [header, rule]
    for r in rows:
        decls = ", ".join(r.get("unfaithful_decls") or [])
        cells = [r.get("item", ""), r.get("kind", ""), r.get("severity", ""),
                 "`%s`" % decls, r.get("ledger_verdict", "(unchallenged)")]
        if multi:
            cells.insert(0, r.get("source_id", ""))
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines.append("")
    return "\n".join(lines)


def _load_anchor_checks(paths: Any, cfg: Any, names: Iterable[str],
                        anchor_check_path: str = "") -> dict:
    """``{source_id: anchor-check payload}`` for the sources in play.

    The withdrawal oracle is per source (``pairs/<source_id>/anchor-check.json``):
    a PAGE_ANCHOR row about the published book must not be withdrawn because the
    2011 draft's anchor check did not examine that item.
    """
    out: dict[str, Any] = {}
    wanted: list[str] = []
    for name in names:
        for sid in cfg.sources_of(name) or []:
            if sid not in wanted:
                wanted.append(sid)
    if not wanted:
        wanted = list(paths.list_paired_sources())
    if anchor_check_path:
        # An explicit path overrides every source: the operator named one file.
        payload = compat.read_json(anchor_check_path) if os.path.exists(anchor_check_path) else None
        return {sid: payload for sid in (wanted or [""])}
    for sid in wanted:
        path = paths.anchor_check(sid)
        if os.path.exists(path):
            out[sid] = compat.read_json(path)
    return out


def route_workspace(
    workspace: str,
    *,
    companions: Iterable[str] | None = None,
    corpus: Iterable[str] | None = None,
    anchor_check_path: str = "",
    write_messages: bool = True,
) -> list[dict]:
    """Route every companion of a workspace; returns one report per companion."""
    cfg = compat.load_workspace(workspace)
    paths = cfg.paths
    names = list(companions or []) or cfg.companion_names()
    corpora = list(corpus or [])
    if not corpora:
        corpus_root = paths.corpus()
        if os.path.isdir(corpus_root):
            corpora = [
                os.path.join(corpus_root, d)
                for d in sorted(os.listdir(corpus_root))
                if os.path.isdir(os.path.join(corpus_root, d))
            ]
    all_findings = findings_mod.load_findings(corpora)
    anchor_checks = _load_anchor_checks(paths, cfg, names, anchor_check_path)

    reports = []
    for name in names:
        loaded = vl.load_ledger_dir(paths, name)
        ledger_rows = loaded["rows"]["accepted"] + loaded["rows"]["refuted"]
        anchor_source = cfg.anchor_source(name)
        routed = route_repo(
            all_findings,
            name,
            source_id=anchor_source,
            source_sha=cfg.source_sha(anchor_source),
            ledger_rows=ledger_rows,
            anchor_check=anchor_checks,
            workspace=workspace,
        )
        path = write_routed(paths, name, routed)
        if write_messages:
            compat.write_text(
                os.path.join(paths.routed, "message.%s.md" % name),
                owner_message(routed, name),
            )
        compat.update_state(
            paths,
            name,
            lambda st, _r=routed, _p=path: st.update(
                {
                    "routed": {
                        "file": _p,
                        "status": _r["meta"]["status"],
                        "counts": _r["meta"]["counts"],
                        "sources": _r["meta"]["sources"],
                    }
                }
            ),
        )
        reports.append({"companion": name, "file": path, "routed": routed})
    return reports


def main(argv: list[str] | None = None) -> int:
    """CLI mirror of ``cf route`` (see scripts/cfcmd/route.py)."""
    ap = argparse.ArgumentParser(description="Route findings to the repo that owns them.")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--companion", action="append", default=[])
    ap.add_argument("--corpus", action="append", default=[])
    ap.add_argument("--emit-upstream", default="", help="directory for the upstream export")
    ap.add_argument("--redact-quotes", action="store_true")
    args = ap.parse_args(argv)

    reports = route_workspace(
        args.workspace,
        companions=args.companion,
        corpus=args.corpus,
    )
    rc = 0
    for rep in reports:
        meta = rep["routed"]["meta"]
        counts = meta["counts"]
        print(
            "%s: %d rows / %d items (%s) -> %s"
            % (rep["companion"], counts["rows"], counts["items"], meta["status"], rep["file"])
        )
        if args.emit_upstream:
            try:
                out = emit_upstream(
                    rep["routed"],
                    args.emit_upstream,
                    companion=rep["companion"],
                    workspace=args.workspace,
                    redact_quotes=args.redact_quotes,
                )
                print("  upstream: %s" % out)
            except UpstreamRefused as exc:
                print("  %s" % exc, file=sys.stderr)
                rc = 2
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
