"""Machine-validate ledger rows against the findings they answer.

Ported from: no single original -- this replaces the hand checking of
/Users/jack/Desktop/tao-differential/{accepted,refuted}.adam.jsonl described in
recovered/b174aa13_L5702_Bash.txt's `_meta` note ("transcribed decisions; no
re-adjudication").  The rules it enforces are the Stage 7 gate from
design/stage_map.md plus the critique's corrections:

  * every row answers a REAL finding, linked by ``finding_id``
    (sha256(run_id, item, kind, sorted unfaithful_decls)) -- not by a 22-char
    label prefix, which is how challenge results were mislinked in batch 1;
  * one vocabulary (references/vocabulary.md): verdict, severity, kind,
    resolution grammar;
  * ``book_sha`` is stamped and matches the campaign's pinned edition -- the
    batch-1 PAGE_ANCHOR findings were judged against the 2011 draft;
  * a CONVENTION verdict must carry a written recovery argument (refuted.adam
    row 2 is the model: the recovery was machine-checked in a scratch file);
  * sub-threshold dissent is data, not noise: it must be present and
    well-formed when a skeptic disagreed (Ex 1.3.12's 0.6 dissent was dropped).

The module is a library first (``validate_rows``) and a CLI second.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Iterable

from ledger import compat

#: rows must carry these
REQUIRED_FIELDS = ("finding_id", "item", "repo", "verdict", "reason", "book_sha")
#: a CONVENTION row's recovery argument has to actually argue something
MIN_RECOVERY_CHARS = 40
#: severity token allowed in a ledger row on top of the finding severities
NO_SEVERITY = "none"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class LedgerError(dict):
    """One validation failure: {code, message, finding_id, item, file}."""

    def __str__(self) -> str:  # pragma: no cover - display only
        where = self.get("file") or ""
        loc = "%s " % where if where else ""
        return "%s[%s] %s (%s)" % (
            loc,
            self.get("code"),
            self.get("message"),
            self.get("item") or self.get("finding_id", "")[:12],
        )


def _err(code: str, message: str, row: dict | None = None, file: str = "") -> LedgerError:
    row = row or {}
    return LedgerError(
        code=code,
        message=message,
        finding_id=row.get("finding_id", ""),
        item=row.get("item", ""),
        file=file,
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_row(
    row: dict,
    *,
    index: dict[str, dict] | None = None,
    book_sha: str = "",
    file: str = "",
) -> list[LedgerError]:
    """Validate one ledger row. Returns a (possibly empty) list of errors."""
    errs: list[LedgerError] = []
    if not isinstance(row, dict):
        return [_err("row_type", "ledger row is not an object", None, file)]

    for field in REQUIRED_FIELDS:
        if not str(row.get(field) or "").strip():
            errs.append(_err("missing_field", "missing %s" % field, row, file))

    verdict = str(row.get("verdict") or "").strip()
    if verdict and verdict not in compat.LEDGER_VERDICTS:
        errs.append(
            _err("verdict", "verdict %r is not in the ledger vocabulary" % verdict, row, file)
        )

    severity = str(row.get("severity") or "").strip()
    if severity and severity not in compat.SEVERITIES and severity != NO_SEVERITY:
        errs.append(_err("severity", "severity %r is not a vocabulary token" % severity, row, file))
    if severity == NO_SEVERITY and verdict and verdict not in compat.CLOSED_VERDICTS:
        errs.append(
            _err(
                "severity",
                "severity 'none' is only legal on a closed verdict, not %s" % verdict,
                row,
                file,
            )
        )

    kind = str(row.get("kind") or "").strip()
    if kind and kind not in compat.FINDING_KINDS:
        errs.append(_err("kind", "kind %r is not a vocabulary token" % kind, row, file))

    conf = row.get("confidence")
    if conf is not None and (not _is_number(conf) or not 0.0 <= float(conf) <= 1.0):
        errs.append(_err("confidence", "confidence must be null or in [0,1]", row, file))

    if "contested" in row and not isinstance(row["contested"], bool):
        errs.append(_err("contested", "contested must be a boolean", row, file))

    for d in row.get("dissent") or []:
        if not isinstance(d, dict):
            errs.append(_err("dissent", "dissent entries must be objects", row, file))
            continue
        if not str(d.get("argument") or "").strip():
            errs.append(_err("dissent", "a dissent entry has no argument", row, file))
        dv = str(d.get("proposed_verdict") or "").strip()
        if dv and dv not in compat.LEDGER_VERDICTS:
            errs.append(
                _err("dissent", "dissent proposes unknown verdict %r" % dv, row, file)
            )
        dc = d.get("confidence")
        if dc is not None and (not _is_number(dc) or not 0.0 <= float(dc) <= 1.0):
            errs.append(_err("dissent", "dissent confidence must be null or in [0,1]", row, file))

    if not compat.resolution_ok(row.get("resolution")):
        errs.append(
            _err(
                "resolution",
                "resolution %r does not match the grammar "
                "(fixed:PR#N | in_progress:<branch> | refuted | argued_correct | out_of_scope)"
                % row.get("resolution"),
                row,
                file,
            )
        )

    if verdict in compat.VERDICTS_REQUIRING_ARGUMENT:
        recovery = str(row.get("recovery_argument") or "").strip()
        if len(recovery) < MIN_RECOVERY_CHARS:
            errs.append(
                _err(
                    "recovery_argument",
                    "%s needs a written recovery argument of at least %d characters"
                    % (verdict, MIN_RECOVERY_CHARS),
                    row,
                    file,
                )
            )

    sha = str(row.get("book_sha") or "").strip()
    if sha and not _SHA_RE.match(sha):
        errs.append(_err("book_sha", "book_sha is not a sha256 hex digest", row, file))
    if book_sha and sha and sha != book_sha:
        errs.append(
            _err(
                "book_sha",
                "book_sha %s is not the campaign edition %s" % (sha[:12], book_sha[:12]),
                row,
                file,
            )
        )

    repo = str(row.get("repo") or "").strip()
    decls = [compat.normalise_decl(d) for d in (row.get("unfaithful_decls") or []) if d]
    if repo:
        wrong = [d for d in decls if compat.decl_repo(d) and compat.decl_repo(d) != repo]
        if wrong:
            errs.append(
                _err("repo", "row filed under %s names %s" % (repo, ", ".join(wrong)), row, file)
            )

    if index is not None:
        fid = str(row.get("finding_id") or "")
        finding = index.get(fid)
        if finding is None:
            errs.append(
                _err(
                    "unlinked",
                    "finding_id %s answers no finding in the corpus" % (fid[:12] or "''"),
                    row,
                    file,
                )
            )
        else:
            if compat.normalise_item(row.get("item")) != compat.normalise_item(finding.get("item")):
                errs.append(
                    _err(
                        "item_mismatch",
                        "row item %r != finding item %r" % (row.get("item"), finding.get("item")),
                        row,
                        file,
                    )
                )
            fdecls = {
                compat.normalise_decl(d) for d in (finding.get("unfaithful_decls") or []) if d
            }
            extra = [d for d in decls if d not in fdecls]
            if extra:
                errs.append(
                    _err(
                        "decl_mismatch",
                        "row names declarations the finding does not: %s" % ", ".join(extra),
                        row,
                        file,
                    )
                )
            if decls and fdecls and not set(decls) & fdecls:
                errs.append(
                    _err("decl_mismatch", "row shares no declaration with its finding", row, file)
                )
    return errs


def validate_rows(
    rows: Iterable[dict],
    *,
    index: dict[str, dict] | None = None,
    book_sha: str = "",
    file: str = "",
) -> list[LedgerError]:
    """Validate a set of rows, including cross-row consistency.

    Two rows may answer the same finding (a finding adjudicated then challenged),
    but they must not contradict each other.
    """
    rows = list(rows)
    errs: list[LedgerError] = []
    for row in rows:
        errs.extend(validate_row(row, index=index, book_sha=book_sha, file=row.get("_file", file)))

    by_id: dict[str, list[dict]] = {}
    for row in rows:
        by_id.setdefault(str(row.get("finding_id") or ""), []).append(row)
    for fid, group in by_id.items():
        verdicts = {str(r.get("verdict") or "") for r in group}
        if len(verdicts) > 1:
            errs.append(
                _err(
                    "contradiction",
                    "finding %s has contradictory ledger verdicts: %s"
                    % (fid[:12], ", ".join(sorted(verdicts))),
                    group[0],
                    file,
                )
            )
    return errs


def ledger_coverage(rows: Iterable[dict], findings: Iterable[dict]) -> dict:
    """Which findings have a ledger row and which do not.

    ``route`` uses this to decide CHALLENGED vs UNCHALLENGED: a routed file may
    only leave the campaign directory when every row it carries was adjudicated.
    """
    answered = {str(r.get("finding_id") or "") for r in rows}
    findings = list(findings)
    covered = [f for f in findings if f["finding_id"] in answered]
    missing = [f for f in findings if f["finding_id"] not in answered]
    return {
        "findings": len(findings),
        "covered": len(covered),
        "missing": len(missing),
        "missing_ids": [f["finding_id"] for f in missing],
        "missing_items": sorted({f["item"] for f in missing}),
    }


def load_ledger_dir(paths: Any, companion: str) -> dict:
    """Load accepted/refuted rows for a companion, tagged with their file."""
    out: dict[str, list[dict]] = {"accepted": [], "refuted": []}
    meta: dict[str, dict] = {}
    for key, path in (
        ("accepted", paths.accepted(companion)),
        ("refuted", paths.refuted(companion)),
    ):
        if not os.path.exists(path):
            continue
        raw = compat.read_jsonl(path)
        meta[key] = compat.meta_row(raw)
        for row in compat.data_rows(raw):
            row = dict(row)
            row["_file"] = path
            out[key].append(row)
    return {"rows": out, "meta": meta}


def validate_ledger_dir(
    paths: Any,
    companion: str,
    *,
    index: dict[str, dict] | None = None,
    book_sha: str = "",
) -> dict:
    """Validate a campaign's ledger files for one companion.

    Adds the file-partition rule on top of ``validate_rows``: accepted.* holds
    verdicts that keep the finding open, refuted.* holds the ones that close it.
    """
    loaded = load_ledger_dir(paths, companion)
    accepted, refuted = loaded["rows"]["accepted"], loaded["rows"]["refuted"]
    errs = validate_rows(accepted + refuted, index=index, book_sha=book_sha)
    for row in accepted:
        v = str(row.get("verdict") or "")
        if v and v not in compat.OPEN_VERDICTS:
            errs.append(_err("partition", "%s belongs in refuted.*" % v, row, row.get("_file", "")))
    for row in refuted:
        v = str(row.get("verdict") or "")
        if v and v not in compat.CLOSED_VERDICTS:
            errs.append(_err("partition", "%s belongs in accepted.*" % v, row, row.get("_file", "")))
    return {
        "companion": companion,
        "accepted": len(accepted),
        "refuted": len(refuted),
        "errors": errs,
        "ok": not errs,
        "meta": loaded["meta"],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: validate one companion's ledger inside a campaign directory."""
    ap = argparse.ArgumentParser(description="Validate ledger rows against findings.")
    ap.add_argument("--campaign", required=True, help="campaign directory")
    ap.add_argument("--companion", required=True, help="companion name (ledger suffix)")
    ap.add_argument(
        "--corpus",
        action="append",
        default=[],
        help="corpus run directory or findings file to link against (repeatable)",
    )
    ap.add_argument("--book-sha", default="", help="override the campaign's pinned book sha")
    args = ap.parse_args(argv)

    from ledger import findings as findings_mod

    campaign = compat.load_campaign(args.campaign)
    paths = campaign.paths
    book_sha = args.book_sha or campaign.book_sha
    sources = args.corpus or (
        [paths.corpus()] if os.path.isdir(paths.corpus()) else []
    )
    index = None
    if sources:
        index = findings_mod.index_by_id(
            findings_mod.load_findings(sources, book_sha=book_sha)
        )
    report = validate_ledger_dir(paths, args.companion, index=index, book_sha=book_sha)
    for err in report["errors"]:
        print(str(err), file=sys.stderr)
    print(
        "%s: %d accepted + %d refuted rows, %d error(s)"
        % (args.companion, report["accepted"], report["refuted"], len(report["errors"]))
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
