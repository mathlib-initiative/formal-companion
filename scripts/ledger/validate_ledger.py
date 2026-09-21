"""Machine-validate ledger rows against the findings they answer.

Ported from: no single original -- this replaces the hand checking of
/Users/jack/Desktop/tao-differential/{accepted,refuted}.adam.jsonl described in
recovered/b174aa13_L5702_Bash.txt's `_meta` note ("transcribed decisions; no
re-adjudication").  The rules it enforces are the Stage 7 gate from
design/stage_map.md plus the critique's corrections:

  * every row answers a REAL finding, linked by ``finding_id``
    (sha256(run_id, source_id, item, kind, sorted unfaithful_decls)) -- not by a
    22-char label prefix, which is how challenge results were mislinked in
    batch 1;
  * one vocabulary (references/vocabulary.md): verdict, severity, kind,
    resolution grammar;
  * ``source_id`` and ``source_sha`` are stamped, the workspace knows that
    source, and the sha is the one it pinned for it -- this is the SOURCE GATE
    at the ledger boundary; the batch-1 PAGE_ANCHOR findings were judged against
    the 2011 draft, and with two editions of one work in a workspace a row that
    does not say which it read cannot be checked at all;
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
REQUIRED_FIELDS = ("finding_id", "item", "source_id", "repo", "verdict", "reason", "source_sha")
#: a CONVENTION row's recovery argument has to actually argue something
MIN_RECOVERY_CHARS = 40
#: severity token allowed in a ledger row on top of the finding severities
NO_SEVERITY = "none"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
#: a git object name: 40 hex, or the 64 of sha256 object format
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


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


def _pin_errors(row: dict, file: str = "") -> list[LedgerError]:
    """Check the shape of the revision pin, when the row carries one.

    The pin is OPTIONAL -- every imported and every pre-pin row has none, and a
    row is not wrong for being unpinned -- but a pin that is present has to be a
    pin: a truncated commit or a fingerprint that is not a digest would make
    ``cf recheck`` report a live finding as changed, which is the one failure mode
    that would make nobody run it again.  ``recheck`` is written by ``cf recheck``
    and must never carry a verdict: a re-check states what the code did.
    """
    errs: list[LedgerError] = []
    commit = str(row.get("repo_commit") or "").strip()
    if commit and not _COMMIT_RE.match(commit):
        errs.append(_err("repo_commit",
                         "repo_commit %r is not a full git object name" % commit[:16],
                         row, file))
    prints = row.get("decl_fingerprints")
    if prints is not None and not isinstance(prints, dict):
        errs.append(_err("decl_fingerprints", "decl_fingerprints is not an object", row, file))
    elif isinstance(prints, dict):
        named = {str(d) for d in (row.get("unfaithful_decls") or []) if d}
        for decl, value in sorted(prints.items()):
            if not _SHA_RE.match(str(value or "")):
                errs.append(_err("decl_fingerprints",
                                 "fingerprint for %s is not a sha256 hex digest" % decl,
                                 row, file))
            if named and str(decl) not in named:
                errs.append(_err("decl_fingerprints",
                                 "fingerprint for %s, which this row does not name" % decl,
                                 row, file))
        if prints and not commit:
            errs.append(_err("repo_commit",
                             "decl_fingerprints without a repo_commit: a fingerprint means "
                             "nothing without the revision it was taken at", row, file))
    block = row.get("recheck")
    if block is not None:
        if not isinstance(block, dict):
            errs.append(_err("recheck", "recheck is not an object", row, file))
        else:
            status = str(block.get("status") or "").strip()
            if status and status not in compat.RECHECK_STATUSES:
                errs.append(_err("recheck",
                                 "recheck status %r is not a vocabulary token (%s)"
                                 % (status, ", ".join(compat.RECHECK_STATUSES)), row, file))
            for field in ("verdict", "resolution"):
                if block.get(field):
                    errs.append(_err("recheck",
                                     "recheck carries a %s: a re-check reports what the code "
                                     "did, it does not adjudicate" % field, row, file))
    return errs


def validate_row(
    row: dict,
    *,
    index: dict[str, dict] | None = None,
    sources: Any = None,
    file: str = "",
) -> list[LedgerError]:
    """Validate one ledger row. Returns a (possibly empty) list of errors.

    ``sources`` is the workspace's ``{source_id: sha256}`` map (a bare sha
    string is accepted for a one-source workspace).  A row with no
    ``source_id``, or with a ``source_sha`` the workspace does not know, is an
    ERROR: that is the source gate at the ledger boundary, and it refuses rather
    than warns.
    """
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

    known = _source_map(sources)
    source_id = str(row.get("source_id") or "").strip()
    sha = str(row.get("source_sha") or "").strip()
    if sha and not _SHA_RE.match(sha):
        errs.append(_err("source_sha", "source_sha is not a sha256 hex digest", row, file))
    if known:
        if source_id and source_id not in known:
            errs.append(
                _err(
                    "source_id",
                    "source_id %r is not a source of this workspace (known: %s)"
                    % (source_id, ", ".join(sorted(known)) or "none"),
                    row,
                    file,
                )
            )
        want = known.get(source_id) if source_id else None
        if want and sha and sha != want:
            errs.append(
                _err(
                    "source_sha",
                    "source_sha %s is not %s's pinned edition %s"
                    % (sha[:12], source_id, want[:12]),
                    row,
                    file,
                )
            )
        if sha and not source_id and sha not in set(known.values()):
            errs.append(
                _err(
                    "source_sha",
                    "source_sha %s belongs to no source of this workspace" % sha[:12],
                    row,
                    file,
                )
            )

    errs.extend(_pin_errors(row, file))

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
            if compat.item_join_key(row.get("item")) != compat.item_join_key(finding.get("item")):
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


def _source_map(sources: Any) -> dict[str, str]:
    """Normalise the ``sources`` argument into ``{source_id: sha256}``.

    A bare sha string is the one-source shorthand; it maps to ``{}`` with the
    sha kept under every id, which is deliberately NOT accepted as an id check
    (there is no id to check against) but still catches a wrong sha.
    """
    if not sources:
        return {}
    if isinstance(sources, str):
        return {}
    out: dict[str, str] = {}
    for sid, value in dict(sources).items():
        if isinstance(value, dict):
            sha = value.get("sha256") or value.get("sha") or value.get("source_sha")
        else:
            sha = value
        out[str(sid)] = str(sha or "")
    return out


def validate_rows(
    rows: Iterable[dict],
    *,
    index: dict[str, dict] | None = None,
    sources: Any = None,
    file: str = "",
) -> list[LedgerError]:
    """Validate a set of rows, including cross-row consistency.

    Two rows may answer the same finding (a finding adjudicated then challenged),
    but they must not contradict each other.
    """
    rows = list(rows)
    errs: list[LedgerError] = []
    for row in rows:
        errs.extend(validate_row(row, index=index, sources=sources,
                                 file=row.get("_file", file)))

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
    only leave the workspace directory when every row it carries was adjudicated.

    The join is on ``(source_id, finding_id)``.  The id already contains the
    source, so the pair is belt and braces -- but a row imported from a v1
    ledger can carry a stale four-part id, and joining on the pair makes that
    show up as an uncovered finding rather than as a false match.

    A finding is covered only when its id is UNAMBIGUOUS.  The id does not hash
    the claim, so two different defects on one declaration of one item share it:
    counting distinct ids answered, one CONVENTION row then closed both and the
    coverage report said 2 findings, 2 covered, 0 missing -- a false clean, in
    the same direction every counting error in this pipeline has gone.  Both
    findings at a colliding id are now counted as missing and the id is named in
    ``collisions`` (``validate_ledger``'s CLI prints an ``[id_collision]`` line
    for each).  A ledger row can only be addressed TO the id, so such an id
    cannot be cleared by adjudicating it harder: the way out is to split the
    finding -- give the second claim its own declaration, item or kind -- so that
    the two claims get two ids.  ``route/route.py`` takes ``collisions`` from here
    and holds those rows back, so the two agree by construction.
    """
    from ledger.findings import claim_key

    def _key(row: dict) -> tuple:
        return (str(row.get("source_id") or ""), str(row.get("finding_id") or ""))

    answered = {_key(r) for r in rows}
    findings = list(findings)
    by_key: dict[tuple, list[dict]] = {}
    for f in findings:
        by_key.setdefault(_key(f), []).append(f)

    covered: list[dict] = []
    missing: list[dict] = []
    collisions: list[dict] = []
    for key, group in by_key.items():
        claims = {claim_key(f) for f in group}
        if len(claims) > 1:
            collisions.append({
                "finding_id": key[1],
                "source_id": key[0],
                "item": str(group[0].get("item") or ""),
                "kind": str(group[0].get("kind") or ""),
                "claims": len(claims),
                "answered": key in answered,
            })
            missing.extend(group)
            continue
        (covered if key in answered else missing).extend(group)
    return {
        "findings": len(findings),
        "covered": len(covered),
        "missing": len(missing),
        "missing_ids": [f["finding_id"] for f in missing],
        "missing_items": sorted({f["item"] for f in missing}),
        "missing_by_source": {
            sid: sorted({f["item"] for f in missing if str(f.get("source_id") or "") == sid})
            for sid in sorted({str(f.get("source_id") or "") for f in missing})
        },
        # ids carrying two claims: one ledger row cannot adjudicate both
        "collisions": sorted(collisions, key=lambda c: (c["source_id"], c["finding_id"])),
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
    sources: Any = None,
) -> dict:
    """Validate a workspace's ledger files for one companion.

    Adds the file-partition rule on top of ``validate_rows``: accepted.* holds
    verdicts that keep the finding open, refuted.* holds the ones that close it.
    """
    loaded = load_ledger_dir(paths, companion)
    accepted, refuted = loaded["rows"]["accepted"], loaded["rows"]["refuted"]
    errs = validate_rows(accepted + refuted, index=index, sources=sources)
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
    """CLI: validate one companion's ledger inside a workspace directory."""
    ap = argparse.ArgumentParser(description="Validate ledger rows against findings.")
    ap.add_argument("--workspace", required=True, help="workspace directory")
    ap.add_argument("--companion", required=True, help="companion name (ledger suffix)")
    ap.add_argument(
        "--corpus",
        action="append",
        default=[],
        help="corpus run directory or findings file to link against (repeatable)",
    )
    ap.add_argument("--source", default="", help="restrict to one source_id")
    args = ap.parse_args(argv)

    from ledger import findings as findings_mod

    workspace = compat.load_workspace(args.workspace)
    paths = workspace.paths
    shas = workspace.source_shas()
    if args.source:
        shas = {args.source: shas.get(args.source, "")}
    corpora = args.corpus or (
        [paths.corpus()] if os.path.isdir(paths.corpus()) else []
    )
    index = None
    collisions: list[dict] = []
    if corpora:
        index = findings_mod.index_by_id(findings_mod.load_findings(corpora),
                                        collisions=collisions)
    report = validate_ledger_dir(paths, args.companion, index=index, sources=shas)
    for err in report["errors"]:
        print(str(err), file=sys.stderr)
    for clash in collisions:
        # one id, two claims: the index kept one of them, so a row addressed to
        # this id answers only the claim it happens to have kept
        print(
            "[id_collision] %s (%s %s) carries %d distinct claims; one ledger row cannot "
            "adjudicate both" % (clash["finding_id"][:12], clash["source_id"], clash["item"],
                                 clash["claims"]),
            file=sys.stderr,
        )
    print(
        "%s: %d accepted + %d refuted rows, %d error(s)"
        % (args.companion, report["accepted"], report["refuted"], len(report["errors"]))
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
