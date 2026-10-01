"""Turn a Workflow result JSON into ledger rows, files and the record markdown.

Ported from: the hand transcription that produced the v1 campaign's
``{accepted,refuted}.adam.jsonl`` and ``adjudicated.adam.md`` from its
adjudication workflow run, plus the challenge result shape of its
challenge workflow ({challenged, findings:[{key, survives_all, verdicts}]}).
None of those artifacts travel with this repo.

Workflow scripts cannot touch the filesystem, so the session saves the tool's
return value to a JSON file and this module transcribes it.  Two rules from the
batch-1 record are mechanical here rather than remembered:

  * dissent BELOW the contested threshold is still written to the row
    (Exercise 1.3.12's skeptic at 0.6 had to be re-surfaced by hand);
  * a CONVENTION verdict carries its recovery argument, or validate_ledger
    rejects the row.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from typing import Any, Iterable

from ledger import compat, migrate
from ledger import validate_ledger as vl

#: a skeptic at or above this confidence makes the row contested
CONTESTED_THRESHOLD = 0.7
#: finding kind -> ledger verdict when an adversarial challenge does NOT refute
SURVIVING_VERDICT = {
    "CONTENT": "CONFIRMED_CONTENT",
    "VACUITY": "CONFIRMED_CONTENT",
    "PROXY": "CONFIRMED_CONTENT",
    "COVERAGE": "CONFIRMED_CONTENT",
    "SUSPECTED_ERRATA": "ERRATA",
    "PAGE_ANCHOR": "CONFIRMED_CONTENT",
}


class Pins:
    """The revision pin ``cf harvest`` wrote for a run, read lazily per run id.

    ``reports/pin.<run-id>.json`` is written by the stage that can be trusted with
    it (harvest computes the commit from ``run.yaml`` and the fingerprints from
    the statement table extracted at that commit).  This class only READS it, and
    only to copy the two fields onto the ledger row -- the row is what outlives
    the corpus, so the pin has to live there too or nothing can be re-checked.

    A run with no pin file (an imported v1 corpus, or a run harvested before the
    pin existed) yields empty values.  That is the ``unpinned`` verdict
    ``cf recheck`` reports, and it is deliberately not filled in from today's
    HEAD: a fingerprint the finding was never judged against is worse than none.
    """

    def __init__(self, paths: Any):
        self._paths = paths
        self._cache: dict[str, dict] = {}

    def _doc(self, run_id: str) -> dict:
        if run_id not in self._cache:
            doc: dict = {}
            path = ""
            getter = getattr(self._paths, "pin_json", None)
            if run_id and callable(getter):
                try:
                    path = str(getter(run_id))
                except Exception:  # an id no path accessor accepts is simply unpinned
                    path = ""
            if path and os.path.exists(path):
                loaded = compat.read_json(path)
                doc = loaded if isinstance(loaded, dict) else {}
            self._cache[run_id] = doc
        return self._cache[run_id]

    def for_row(self, run_id: str, decls: Iterable[str], companion: str = "") -> dict:
        """``{repo_commit, decl_fingerprints}`` for one row's declarations."""
        doc = self._doc(str(run_id or ""))
        entries = doc.get("decls")
        entries = entries if isinstance(entries, dict) else {}
        out: dict[str, str] = {}
        commit = ""
        for decl in decls:
            entry = entries.get(str(decl))
            if isinstance(entry, dict) and entry.get("decl_fingerprint"):
                out[str(decl)] = str(entry["decl_fingerprint"])
                commit = commit or str(entry.get("repo_commit") or "")
        if not commit and companion:
            # No declaration of this row was fingerprinted, but the run still
            # recorded which revision the companion was at: keep it, so the row
            # says what it was judged against even when it cannot say how.
            companions = doc.get("companions")
            block = (companions or {}).get(companion) if isinstance(companions, dict) else None
            commit = str((block or {}).get("repo_commit") or "")
        return {"repo_commit": commit, "decl_fingerprints": out}


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _finding_fields(finding: dict | None) -> dict:
    if not finding:
        return {"kind": "", "unfaithful_decls": [], "severity": "", "run_id": ""}
    return {
        "kind": str(finding.get("kind") or ""),
        "unfaithful_decls": list(finding.get("unfaithful_decls") or []),
        "severity": str(finding.get("severity") or ""),
        "run_id": str(finding.get("run_id") or ""),
    }


def _base_row(
    *,
    finding_id: str,
    item: str,
    companion: str,
    finding: dict | None,
    source_id: str,
    source_sha: str,
    method: str,
    at: str,
    pins: "Pins | None" = None,
) -> dict:
    fields = _finding_fields(finding)
    decls = [
        d
        for d in fields["unfaithful_decls"]
        if not companion or compat.decl_repo(d) in ("", companion)
    ]
    # The revision pin travels with the row, because the row is what outlives the
    # corpus: a ledger row with no commit and no fingerprint is a claim nobody can
    # re-check, close or expire later (that is what `cf recheck` reads).
    pin = pins.for_row(fields["run_id"], decls, companion) if pins else {}
    return {
        "finding_id": finding_id,
        "run_id": fields["run_id"],
        # The finding's OWN source wins: a run adjudicates one source, but a
        # ledger file accumulates rows from several runs, and stamping them all
        # with the caller's default would file a 2011-draft row under the
        # published edition.
        "source_id": str((finding or {}).get("source_id") or source_id or ""),
        "source_sha": str((finding or {}).get("source_sha") or source_sha or ""),
        "item": compat.normalise_item(item or (finding or {}).get("item")),
        "repo": companion,
        "kind": fields["kind"],
        "unfaithful_decls": decls,
        "contested": False,
        "dissent": [],
        "method": method,
        "schema": "ledger-v2",
        "at": at,
        # "" / {} rather than absent: a reader must be able to tell a row that was
        # written without a pin from one whose run had none, and both from a row
        # that predates the field entirely (that one has no key at all).
        "repo_commit": pin.get("repo_commit", ""),
        "decl_fingerprints": pin.get("decl_fingerprints", {}),
    }


def rows_from_adjudication(
    result: dict,
    *,
    companion: str,
    source_id: str = "",
    source_sha: str = "",
    index: dict[str, dict] | None = None,
    method: str = "workflow:adjudicate",
    at: str = "",
    contested_threshold: float = CONTESTED_THRESHOLD,
    pins: Pins | None = None,
) -> list[dict]:
    """Map adjudicate.workflow.js output onto ledger rows."""
    index = index or {}
    rows: list[dict] = []
    for entry in result.get("findings") or []:
        if not isinstance(entry, dict):
            continue
        reconciled = entry.get("reconciled") or {}
        fid = str(entry.get("finding_id") or reconciled.get("finding_id") or "")
        finding = index.get(fid)
        row = _base_row(
            finding_id=fid,
            item=entry.get("item") or reconciled.get("item") or "",
            companion=companion,
            finding=finding,
            source_id=source_id,
            source_sha=source_sha,
            method=method,
            at=at,
            pins=pins,
        )
        row["verdict"] = migrate.map_verdict(reconciled.get("verdict"))
        row["severity"] = str(reconciled.get("severity") or row.get("severity") or "")
        row["confidence"] = _num(reconciled.get("confidence"))
        row["summary"] = str(entry.get("concept") or (finding or {}).get("concept") or "")
        row["reason"] = str(reconciled.get("rationale") or "")
        row["fix_effort"] = str(reconciled.get("fix_effort") or "")
        if reconciled.get("pr_plan"):
            row["pr_plan"] = reconciled["pr_plan"]
        if reconciled.get("refutation_text"):
            row["refutation_text"] = reconciled["refutation_text"]
        if reconciled.get("lens_disagreement"):
            row["lens_disagreement"] = reconciled["lens_disagreement"]
        if reconciled.get("pr_recommended") is not None:
            row["pr_recommended"] = bool(reconciled["pr_recommended"])

        dissent = []
        contested = bool(entry.get("contested", False))
        for skeptic in entry.get("skeptics") or []:
            if not isinstance(skeptic, dict) or not skeptic.get("overturned"):
                continue
            conf = _num(skeptic.get("confidence"))
            dissent.append(
                {
                    "role": str(skeptic.get("role") or skeptic.get("label") or "skeptic"),
                    "confidence": conf,
                    "proposed_verdict": migrate.map_verdict(skeptic.get("proposed_verdict")),
                    "argument": str(skeptic.get("argument") or ""),
                }
            )
            if conf is not None and conf >= contested_threshold:
                contested = True
        row["dissent"] = dissent
        row["contested"] = contested
        if row["verdict"] == "CONVENTION":
            row["recovery_argument"] = str(
                reconciled.get("recovery_argument")
                or reconciled.get("refutation_text")
                or reconciled.get("rationale")
                or ""
            )
        if entry.get("lenses"):
            row["lens_verdicts"] = [
                {
                    "lens": str(l.get("lens") or ""),
                    "verdict": migrate.map_verdict(l.get("verdict")),
                    "confidence": _num(l.get("confidence")),
                }
                for l in entry["lenses"]
                if isinstance(l, dict)
            ]
        rows.append(row)
    return rows


def rows_from_challenge(
    result: dict,
    *,
    companion: str,
    source_id: str = "",
    source_sha: str = "",
    index: dict[str, dict] | None = None,
    method: str = "workflow:challenge",
    at: str = "",
    pins: Pins | None = None,
) -> list[dict]:
    """Map challenge.workflow.js output onto ledger rows (refute-by-default)."""
    index = index or {}
    rows: list[dict] = []
    for entry in result.get("findings") or []:
        if not isinstance(entry, dict):
            continue
        fid = str(entry.get("finding_id") or "")
        finding = index.get(fid)
        verdicts = [v for v in (entry.get("verdicts") or []) if isinstance(v, dict)]
        survives_all = bool(entry.get("survives_all")) and bool(verdicts)
        row = _base_row(
            finding_id=fid,
            item=entry.get("item") or "",
            companion=companion,
            finding=finding,
            source_id=source_id,
            source_sha=source_sha,
            method=method,
            at=at,
            pins=pins,
        )
        kind = row.get("kind") or str(entry.get("kind") or "")
        if survives_all:
            row["verdict"] = SURVIVING_VERDICT.get(kind, "CONFIRMED_CONTENT")
        else:
            refuters = [v for v in verdicts if not v.get("ruling_survives")]
            classes = [migrate.map_verdict(v.get("classification")) for v in refuters]
            classes = [c for c in classes if c in compat.LEDGER_VERDICTS and c != "CONFIRMED_CONTENT"]
            row["verdict"] = classes[0] if classes else "REFUTED"
        row["severity"] = str(entry.get("severity") or row.get("severity") or "")
        if row["verdict"] in ("REFUTED", "CONVENTION") and not row["severity"]:
            row["severity"] = "none"
        row["confidence"] = _num(entry.get("confidence"))
        row["summary"] = str(entry.get("concept") or (finding or {}).get("concept") or "")
        row["reason"] = "\n\n".join(
            str(v.get("evidence") or "").strip() for v in verdicts if v.get("evidence")
        )
        if row["verdict"] == "CONVENTION":
            row["recovery_argument"] = row["reason"]
        # a split panel (one refuter for, one against) is contested by definition
        stances = {bool(v.get("ruling_survives")) for v in verdicts}
        row["contested"] = len(stances) > 1
        row["dissent"] = [
            {
                "role": "refuter",
                "confidence": _num(v.get("confidence")),
                "proposed_verdict": migrate.map_verdict(v.get("classification")),
                "argument": str(v.get("corrected_ruling") or v.get("evidence") or ""),
            }
            for v in verdicts
            if bool(v.get("ruling_survives")) != survives_all
        ]
        rows.append(row)
    return rows


def split_rows(rows: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Partition rows into (accepted, refuted) by verdict."""
    accepted, refuted = [], []
    for row in rows:
        verdict = str(row.get("verdict") or "")
        (refuted if verdict in compat.CLOSED_VERDICTS else accepted).append(row)
    key = lambda r: (r.get("item", ""), r.get("finding_id", ""))
    return sorted(accepted, key=key), sorted(refuted, key=key)


def _counts(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    out: dict[str, Any] = {"rows": len(rows), "items": len({r.get("item") for r in rows})}
    for field in ("verdict", "severity"):
        tally: dict[str, int] = {}
        for row in rows:
            tally[str(row.get(field) or "")] = tally.get(str(row.get(field) or ""), 0) + 1
        out["by_%s" % field] = tally
    out["contested"] = sum(1 for r in rows if r.get("contested"))
    return out


#: sections of the record read from a result rather than the rows; a pass whose
#: result has none (every challenge pass) keeps the earlier record's
RESULT_SECTIONS = (
    ("definitions", "Definition readings"),
    ("uncovered", "Book definitions with no native definition"),
    ("critic", "Completeness critic"),
)


def _earlier_section(md: str, title: str) -> list[str]:
    lines = md.splitlines()
    heading = "## %s" % title
    if heading not in lines:
        return []
    stops = {"## %s" % t for _, t in RESULT_SECTIONS}
    end = start = lines.index(heading)
    while end + 1 < len(lines) and lines[end + 1] not in stops \
            and not lines[end + 1].startswith("Refutation details: "):
        end += 1
    return lines[start:end + 1]


def render_markdown(companion: str, rows: Iterable[dict], result: dict, meta: dict,
                    previous: str = "") -> str:
    """Render adjudicated.<companion>.md -- the human record of the ledger."""
    rows = sorted(rows, key=lambda r: (r.get("item", ""), r.get("finding_id", "")))
    counts = _counts(rows)
    lines: list[str] = []
    lines.append("# %s — adjudication record (%s)" % (companion, meta.get("at", "")))
    lines.append("")
    lines.append(
        "Result file: `%s`, method `%s`."
        % (meta.get("result_file", ""), meta.get("method", ""))
    )
    passes: dict[tuple, int] = {}
    for row in rows:
        key = (str(row.get("at") or ""), str(row.get("method") or ""))
        passes[key] = passes.get(key, 0) + 1
    lines.append("Rows by pass: " + "; ".join(
        "`%s` %s (%d)" % (method, at, n) for (at, method), n in sorted(passes.items())))
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        by_source.setdefault(str(row.get("source_id") or "(unstamped)"), []).append(row)
    lines.append(
        "Sources adjudicated: "
        + "; ".join("`%s` (%s…, %d row%s)"
                    % (sid, str((rs[0].get("source_sha") or ""))[:12], len(rs),
                       "" if len(rs) == 1 else "s")
                    for sid, rs in sorted(by_source.items()))
    )
    lines.append(
        "Process per row: three independent lenses (book-literal, Lean-semantics, "
        "convention-policy) → reconciler → two opposing skeptics; dissent below the "
        "contested threshold is recorded in the row."
    )
    # One table per source. An item label means a different item in a different
    # source, so a single table sorted by item would interleave two books'
    # `Exercise 1.3.19` rows as though they were the same row twice.
    for sid, group in sorted(by_source.items()):
        lines.append("")
        lines.append("### %s" % sid)
        lines.append("")
        lines.append("| item | verdict | severity | contested | outcome |")
        lines.append("|---|---|---|---|---|")
        for row in group:
            outcome = str(
                row.get("resolution")
                or row.get("pr_plan")
                or row.get("refutation_text")
                or row.get("summary")
                or ""
            )
            outcome = outcome.replace("|", "\\|").replace("\n", " ")
            if len(outcome) > 180:
                outcome = outcome[:177] + "..."
            lines.append(
                "| %s | %s | %s | %s | %s |"
                % (
                    row.get("item", ""),
                    row.get("verdict", ""),
                    row.get("severity", ""),
                    "yes" if row.get("contested") else "",
                    outcome,
                )
            )
    lines.append("")
    verdicts = ", ".join(
        "%d %s" % (n, v) for v, n in sorted(counts["by_verdict"].items()) if v
    )
    lines.append(
        "**Tally:** %d rows over %d items — %s. %d contested."
        % (counts["rows"], counts["items"], verdicts, counts["contested"])
    )
    lines.append("")
    dissenting = [r for r in rows if r.get("dissent")]
    if dissenting:
        lines.append("## Dissent (recorded even below the contested threshold)")
        lines.append("")
        for row in dissenting:
            for d in row["dissent"]:
                arg = str(d.get("argument") or "").replace("\n", " ")
                lines.append(
                    "- %s — %s at %s proposed %s: %s"
                    % (
                        row.get("item", ""),
                        d.get("role", "skeptic"),
                        d.get("confidence"),
                        d.get("proposed_verdict", ""),
                        arg[:400],
                    )
                )
        lines.append("")
    for key, title in RESULT_SECTIONS[:2]:
        entries = result.get(key) or []
        if not entries:
            lines.extend(_earlier_section(previous, title))
            continue
        lines.append("## %s" % title)
        lines.append("")
        for entry in entries:
            merged = entry.get("merged") if isinstance(entry, dict) else None
            payload = merged or entry
            if not isinstance(payload, dict):
                continue
            lines.append(
                "- **%s** — %s"
                % (
                    payload.get("label", ""),
                    payload.get("overall") or payload.get("classification") or "",
                )
            )
        lines.append("")
    if result.get("critic"):
        lines.append("## Completeness critic")
        lines.append("")
        lines.append(str(result["critic"]).strip())
        lines.append("")
    else:
        lines.extend(_earlier_section(previous, RESULT_SECTIONS[2][1]))
    lines.append("Refutation details: `refuted.%s.jsonl`; open rows: `accepted.%s.jsonl`." % (companion, companion))
    lines.append("")
    return "\n".join(lines)


def transcribe(
    result_path: str,
    *,
    workspace: str,
    companion: str,
    source_id: str = "",
    source_sha: str = "",
    index: dict[str, dict] | None = None,
    kind: str = "adjudicate",
    at: str = "",
    validate: bool = True,
    sources: Any = None,
) -> dict:
    """Read a workflow result file and write the companion's ledger files.

    ``kind`` is which workflow produced the file (``adjudicate``/``challenge``);
    it used to be called ``source``, which now means something else entirely.
    ``source_id``/``source_sha`` are the fallbacks for rows whose finding does
    not carry its own.
    """
    result = compat.read_json(result_path)
    if isinstance(result, dict) and "result" in result and isinstance(result["result"], dict):
        result = result["result"]
    ws = compat.load_workspace(workspace)
    paths = ws.paths
    if not at:
        stamp = _dt.datetime.fromtimestamp(os.path.getmtime(result_path), _dt.timezone.utc)
        at = stamp.strftime("%Y-%m-%d")
    source_id = source_id or str(result.get("source_id") or "")
    source_sha = source_sha or str(result.get("source_sha") or result.get("book_sha") or "")
    if source_id and not source_sha:
        source_sha = ws.source_sha(source_id)
    if sources is None:
        sources = ws.source_shas()
    method = "workflow:%s" % kind
    if kind == "challenge":
        rows = rows_from_challenge(
            result, companion=companion, source_id=source_id, source_sha=source_sha,
            index=index, method=method, at=at, pins=Pins(paths)
        )
    else:
        rows = rows_from_adjudication(
            result, companion=companion, source_id=source_id, source_sha=source_sha,
            index=index, method=method, at=at, pins=Pins(paths)
        )
    # a pass's verdict replaces any earlier row for its finding, in either file
    earlier = [r for path in (paths.accepted(companion), paths.refuted(companion))
               if os.path.exists(path) for r in compat.data_rows(compat.read_jsonl(path))]
    ids = {str(r.get("finding_id") or "") for r in rows}
    kept = [r for r in earlier if str(r.get("finding_id") or "") not in ids]
    accepted, refuted = split_rows(kept + rows)
    ledger = accepted + refuted
    meta_common = {
        "companion": companion,
        "sources": sorted({str(r.get("source_id") or "") for r in ledger if r.get("source_id")}),
        "source_shas": {str(r.get("source_id")): str(r.get("source_sha") or "")
                        for r in ledger if r.get("source_id")},
        "method": method,
        "at": at,
        "result_file": os.path.abspath(result_path),
        "workflow_run_id": result.get("run_id") or result.get("workflow_run_id") or "",
    }
    compat.write_jsonl(
        paths.accepted(companion),
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in accepted],
        meta=dict(meta_common, file="accepted", counts=_counts(accepted)),
    )
    compat.write_jsonl(
        paths.refuted(companion),
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in refuted],
        meta=dict(meta_common, file="refuted", counts=_counts(refuted)),
    )
    md_path = paths.adjudicated_md(companion)
    previous = ""
    if os.path.exists(md_path):
        with open(md_path, encoding="utf-8") as fh:
            previous = fh.read()
    compat.write_text(md_path, render_markdown(companion, ledger, result, meta_common, previous))

    this_accepted, this_refuted = split_rows(rows)
    report = {
        "companion": companion,
        "accepted": len(this_accepted),
        "refuted": len(this_refuted),
        "contested": sum(1 for r in rows if r.get("contested")),
        "replaced": len(earlier) - len(kept),
        "ledger": {
            "accepted": len(accepted),
            "refuted": len(refuted),
            "contested": sum(1 for r in ledger if r.get("contested")),
        },
        "files": [
            paths.accepted(companion),
            paths.refuted(companion),
            paths.adjudicated_md(companion),
        ],
        "errors": [],
    }
    if validate:
        errs = vl.validate_rows(rows, index=index, sources=sources)
        report["errors"] = errs
        report["ok"] = not errs
    return report


def summary(report: dict) -> str:
    """What one transcription added, and what the companion's ledger now holds."""
    return (
        "%s: transcribed %d accepted, %d refuted, %d contested (%d earlier row(s) replaced); "
        "ledger now %d accepted, %d refuted, %d contested"
        % (report["companion"], report["accepted"], report["refuted"], report["contested"],
           report["replaced"], report["ledger"]["accepted"], report["ledger"]["refuted"],
           report["ledger"]["contested"])
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: transcribe a saved workflow result into the workspace ledger."""
    ap = argparse.ArgumentParser(description="Transcribe a workflow result into ledger rows.")
    ap.add_argument("result", help="JSON file holding the Workflow tool's return value")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--companion", required=True)
    ap.add_argument("--from", dest="kind", choices=("adjudicate", "challenge"),
                    default="adjudicate", help="which workflow produced the result file")
    ap.add_argument("--corpus", action="append", default=[])
    ap.add_argument("--source", dest="source_id", default="",
                    help="source_id for rows whose finding does not carry one")
    ap.add_argument("--at", default="", help="date stamp (default: the result file's mtime)")
    args = ap.parse_args(argv)

    from ledger import findings as findings_mod

    ws = compat.load_workspace(args.workspace)
    index = None
    if args.corpus:
        index = findings_mod.index_by_id(
            findings_mod.load_findings(args.corpus, source_id=args.source_id)
        )
    report = transcribe(
        args.result,
        workspace=args.workspace,
        companion=args.companion,
        source_id=args.source_id,
        source_sha=ws.source_sha(args.source_id) if args.source_id else "",
        index=index,
        kind=args.kind,
        at=args.at,
    )
    for err in report.get("errors") or []:
        print(str(err), file=sys.stderr)
    print("%s -> %s" % (summary(report), ", ".join(report["files"])))
    return 0 if not report.get("errors") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
