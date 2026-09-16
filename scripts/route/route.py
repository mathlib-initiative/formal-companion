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

2.  ``_meta`` is structured (status / rule / book_sha / counts) instead of two
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
#: provenance value written by pair/anchor_check.py for mechanical anchor rows
MECHANICAL_SOURCE = "anchor_check"

STATUS_CHALLENGED = compat.STATUS_CHALLENGED
STATUS_UNCHALLENGED = compat.STATUS_UNCHALLENGED


class UpstreamRefused(RuntimeError):
    """Raised when an upstream export is attempted on an UNCHALLENGED file."""


def challenge_command(campaign: str, companion: str) -> str:
    """The exact command that turns UNCHALLENGED into CHALLENGED."""
    return (
        "python3 scripts/cf.py adjudicate --campaign %s --companion %s "
        "--challenge-only --emit-args" % (campaign, companion)
    )


def _anchor_index(anchor_check: Any) -> dict[str, dict]:
    """Index a pair/anchor_check.json payload by normalised item."""
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
            out[compat.normalise_item(row["item"])] = row
    return out


def _withdrawal(row: dict, anchors: dict[str, dict]) -> str:
    """Return a withdrawal reason for *row*, or '' when it should be routed."""
    kind = str(row.get("kind") or "")
    if kind not in compat.MECHANICAL_KINDS:
        return ""
    if str(row.get("provenance") or "") == MECHANICAL_SOURCE:
        anchor = anchors.get(compat.normalise_item(row.get("item")))
        if anchor is not None and anchor.get("agrees") is True:
            return (
                "the pinned edition agrees with the repo anchor "
                "(pair/anchor-check.json)"
            )
        return ""
    anchor = anchors.get(compat.normalise_item(row.get("item")))
    if anchor is not None and anchor.get("agrees") is True:
        return "contradicted by the pinned edition (pair/anchor-check.json)"
    return (
        "%s is mechanical-only in vocabulary v2; re-derive with "
        "`python3 scripts/cf.py pair --anchor-check`" % kind
    )


def route_repo(
    findings: Iterable[dict],
    companion: str,
    *,
    book_sha: str = "",
    ledger_rows: Iterable[dict] | None = None,
    anchor_check: Any = None,
    campaign: str = "",
) -> dict:
    """Route every finding that names *companion* and decide the file's status.

    Returns ``{"rows": [...], "meta": {...}}``; the caller writes it out.
    """
    rows_in = list(findings)
    mine = findings_mod.rows_naming(rows_in, companion)
    anchors = _anchor_index(anchor_check)

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
            "book_sha": f.get("book_sha", "") or book_sha,
            "provenance": f.get("provenance", ""),
            "source_file": f.get("source_file", ""),
        }
        if f.get("witness"):
            row["witness"] = f["witness"]
        if f.get("bridge") is not None:
            row["bridge"] = f["bridge"]
        reason = _withdrawal(f, anchors)
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

    shas = {r.get("book_sha") for r in routed if r.get("book_sha")}
    meta = {
        "repo": companion,
        "status": status,
        "rule": ROUTING_RULE,
        "book_sha": book_sha or (sorted(shas)[0] if shas else ""),
        "counts": {
            "rows": len(open_rows),
            "items": len({r["item"] for r in open_rows}),
            "withdrawn": len(routed) - len(open_rows),
            "challenged": len(open_rows) - len(unchallenged),
            "unchallenged": len(unchallenged),
            "by_kind": by_kind,
            "by_severity": by_severity,
            "runs": sorted({r.get("run_id", "") for r in open_rows if r.get("run_id")}),
        },
        "schema": "routed-v2",
    }
    if len(shas) > 1:
        meta["book_sha_conflict"] = sorted(shas)
    if status == STATUS_UNCHALLENGED:
        meta["upstream"] = "REFUSED while UNCHALLENGED"
        meta["challenge_command"] = challenge_command(campaign or "<campaign>", companion)
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
    campaign: str = "",
    redact_quotes: bool = False,
    open_only: bool = True,
) -> str:
    """Write the upstream report for a routed file.

    Refuses -- loudly -- while ``_meta.status`` is UNCHALLENGED: a first-pass
    finding is wrong about 40% of the time, and 32 unchallenged findings were
    held back from teorth for exactly this reason.  It also refuses a file whose
    rows were judged against more than one edition of the book.
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
                meta.get("challenge_command") or challenge_command(campaign or "<campaign>", companion),
            )
        )
    if meta.get("book_sha_conflict"):
        raise UpstreamRefused(
            "refusing to emit an upstream report for %s: its rows were judged against "
            "%d different editions (%s). Re-run the stages that produced the odd one."
            % (companion, len(meta["book_sha_conflict"]),
               ", ".join(s[:12] for s in meta["book_sha_conflict"]))
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
        "Book edition: `%s`. Routing rule: %s." % ((meta.get("book_sha") or "")[:16], meta.get("rule", "")),
        "",
    ]
    if meta.get("status") != STATUS_CHALLENGED:
        lines += [
            "**These rows have NOT been challenged and must not be sent as findings.**",
            "Run: `%s`" % (meta.get("challenge_command") or ""),
            "",
        ]
    lines += ["| item | kind | severity | declaration | verdict |", "|---|---|---|---|---|"]
    for r in rows:
        decls = ", ".join(r.get("unfaithful_decls") or [])
        lines.append(
            "| %s | %s | %s | `%s` | %s |"
            % (
                r.get("item", ""),
                r.get("kind", ""),
                r.get("severity", ""),
                decls,
                r.get("ledger_verdict", "(unchallenged)"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def route_campaign(
    campaign: str,
    *,
    companions: Iterable[str] | None = None,
    corpus: Iterable[str] | None = None,
    book_sha: str = "",
    anchor_check_path: str = "",
    write_messages: bool = True,
) -> list[dict]:
    """Route every companion of a campaign; returns one report per companion."""
    cfg = compat.load_campaign(campaign)
    paths = cfg.paths
    book_sha = book_sha or cfg.book_sha
    names = list(companions or []) or cfg.companion_names()
    sources = list(corpus or [])
    if not sources:
        corpus_root = paths.corpus()
        if os.path.isdir(corpus_root):
            sources = [
                os.path.join(corpus_root, d)
                for d in sorted(os.listdir(corpus_root))
                if os.path.isdir(os.path.join(corpus_root, d))
            ]
    all_findings = findings_mod.load_findings(sources, book_sha=book_sha)
    anchor_check = None
    anchor_path = anchor_check_path or str(
        getattr(paths, "anchor_check_json", os.path.join(paths.pairs, "anchor-check.json"))
    )
    if os.path.exists(anchor_path):
        anchor_check = compat.read_json(anchor_path)

    reports = []
    for name in names:
        loaded = vl.load_ledger_dir(paths, name)
        ledger_rows = loaded["rows"]["accepted"] + loaded["rows"]["refuted"]
        routed = route_repo(
            all_findings,
            name,
            book_sha=book_sha,
            ledger_rows=ledger_rows,
            anchor_check=anchor_check,
            campaign=campaign,
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
                        "book_sha": _r["meta"]["book_sha"],
                    }
                }
            ),
        )
        reports.append({"companion": name, "file": path, "routed": routed})
    return reports


def main(argv: list[str] | None = None) -> int:
    """CLI mirror of ``cf route`` (see scripts/cfcmd/route.py)."""
    ap = argparse.ArgumentParser(description="Route findings to the repo that owns them.")
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--companion", action="append", default=[])
    ap.add_argument("--corpus", action="append", default=[])
    ap.add_argument("--book-sha", default="")
    ap.add_argument("--emit-upstream", default="", help="directory for the upstream export")
    ap.add_argument("--redact-quotes", action="store_true")
    args = ap.parse_args(argv)

    reports = route_campaign(
        args.campaign,
        companions=args.companion,
        corpus=args.corpus,
        book_sha=args.book_sha,
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
                    campaign=args.campaign,
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
