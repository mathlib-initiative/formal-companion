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

4.  The routed row CARRIES ITS EVIDENCE.  A routed file used to hold the item,
    the kind, the severity, the declaration names and a verdict -- so the owner
    message rendered from it was a five-column table with no book sentence, no
    printed page and no ``file:line``, which is nothing a companion maintainer
    can check.  Every row now also carries the item's printed page (from the
    source's ``items.json``) and, per declaration, the file, the line and the
    statement head (from the companion's ``statements.jsonl``), so both emitters
    -- the owner message and ``cf route --github`` -- render evidence rather than
    a claim.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from typing import Any, Iterable, Mapping

from ledger import compat
from ledger import findings as findings_mod
from ledger import validate_ledger as vl
from ledger import workflow_args

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


class PolicyRefused(ValueError):
    """Raised when a destination or quote policy is not one this skill implements."""


def challenge_command(workspace: str, companion: str) -> str:
    """The exact command that turns UNCHALLENGED into CHALLENGED.

    ONE spelling (``cf``, the ``bin/cf`` shim -- symlink it onto PATH with
    ``ln -s <skill>/bin/cf ~/.local/bin/cf``) with the
    workspace RESOLVED.  This string is not only printed: it is written into
    ``ledger/routed/findings.<name>.jsonl`` and into the owner-message draft, so
    the old ``python3 scripts/cf.py adjudicate --workspace <workspace> ...``
    shipped a command with a literal placeholder and a path relative to the skill
    checkout -- neither of which resolves from the workspace the operator is
    standing in -- to the person the draft is addressed to.

    With no workspace to resolve the flag is left OFF rather than filled with a
    placeholder: ``cf`` then falls back to ``$CF_WORKSPACE`` or the current
    directory, which is what ``cf --help`` promises, so the command still runs.
    """
    where = " --workspace %s" % os.path.abspath(workspace) if workspace else ""
    return ("cf adjudicate%s --companion %s --challenge-only --emit-args"
            % (where, companion))


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

    the design critique, correction 2: "Route them as mechanical low-severity rows from
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
        "this item for %s; re-derive with `cf pair`"
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


#: What a colliding ``finding_id`` cannot be given: one ledger row.
ID_COLLISION_HELD_BACK = (
    "held back: %d findings share this finding_id, and a ledger row can only be addressed to the "
    "id, so one row cannot adjudicate more than one of them. Give each further claim its own "
    "declaration, item or kind so they get their own ids, then re-run `cf harvest <run-id>` and "
    "`cf route`."
)


def item_pages(paths: Any, source_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{source_id: {item join key: printed page}}`` from each ``items.json``.

    The printed page is the one fact an owner needs to open the book at the
    sentence being quoted, and it is already computed (``sources/<id>/items.json``,
    ``page = pdf_page - offset``).  Keyed by ``item_join_key`` because this is a
    MATCH between two generations of item label (CLAUDE.md S4).
    """
    out: dict[str, dict[str, Any]] = {}
    for sid in source_ids:
        if not sid:
            continue
        path = str(paths.source_items(sid))
        if not os.path.exists(path):
            continue
        try:
            doc = compat.read_json(path)
        except Exception:  # noqa: BLE001 - an unreadable items.json is a missing page, not a crash
            continue
        rows = doc.get("items") if isinstance(doc, dict) else doc
        index: dict[str, Any] = {}
        for row in rows or []:
            if isinstance(row, dict) and row.get("item"):
                index.setdefault(compat.item_join_key(row["item"]), row.get("page"))
        out[str(sid)] = index
    return out


def decl_sites(statements: Any, decls: Iterable[str]) -> list[dict]:
    """``file``/``line``/``statement`` for each declaration, from statements.jsonl.

    ``found: false`` is kept rather than dropped: a declaration a finding names
    that the statement table does not hold is itself a fact the owner should see,
    and silently omitting it would make the deliverable look complete.  No index
    at all yields nothing, so a caller that asked for no evidence gets the rows it
    always got rather than a row per declaration saying "not found".
    """
    if not statements:
        return []
    index = (statements or {}).get("by_decl") or {}
    out: list[dict] = []
    for decl in decls or []:
        name = compat.normalise_decl(decl)
        if not name:
            continue
        bare = name.split(":", 1)[1] if ":" in name else name
        row = index.get(name) or index.get(bare) or {}
        site = {"decl": name, "found": bool(row)}
        if row:
            site["file"] = row.get("file")
            site["line"] = row.get("line")
            head = " ".join(str(row.get("statement") or "").split())
            if head:
                site["statement"] = head[:400]
            if row.get("page_anchor") is not None:
                site["page_anchor"] = row["page_anchor"]
        out.append(site)
    return out


def route_repo(
    findings: Iterable[dict],
    companion: str,
    *,
    source_id: str = "",
    source_sha: str = "",
    ledger_rows: Iterable[dict] | None = None,
    anchor_check: Any = None,
    workspace: str = "",
    pages: Mapping[str, Mapping[str, Any]] | None = None,
    statements: Any = None,
) -> dict:
    """Route every finding that names *companion* and decide the file's status.

    ``source_id``/``source_sha`` are the fallbacks for a finding that carries
    none; a row's own source always wins, and the meta records every source the
    routed file touches.  A routed file may span sources (one companion audited
    against two books), so the edition is a per-row fact, not a file-level one.

    ``pages`` (``{source_id: {item join key: printed page}}``, from
    :func:`item_pages`) and ``statements`` (one companion's
    ``workflow_args.load_statements`` index) are the evidence the row carries;
    both are optional, and a caller that passes neither gets exactly the rows it
    got before, minus no key.

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

    # The other half of the id-collision fix (validate_ledger.ledger_coverage's
    # docstring named this file as the remaining hole): the lookup above is
    # first-wins by id, so two findings sharing a colliding finding_id were BOTH
    # handed the single ledger row addressed to it and both marked CHALLENGED --
    # a file that then passed the upstream gate carrying one claim nobody
    # adjudicated.  ledger_coverage counts such a finding uncovered; route now
    # agrees with it by construction and holds the rows back with the reason.
    coverage = vl.ledger_coverage(list(ledger_rows or []), mine)
    collided = {(str(c.get("source_id") or ""), str(c.get("finding_id") or ""))
                for c in coverage["collisions"]}
    claims_at = {(str(c.get("source_id") or ""), str(c.get("finding_id") or "")): c.get("claims", 2)
                 for c in coverage["collisions"]}

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
        page = (pages or {}).get(row_source) or {}
        printed = page.get(compat.item_join_key(row["item"]))
        if printed is not None:
            row["page"] = printed
        sites = decl_sites(statements, row["unfaithful_decls"])
        if sites:
            row["decl_sites"] = sites
        row_anchors, row_checked = _oracle(row_source)
        reason = _withdrawal(f, row_anchors, row_checked, row_source)
        if reason:
            row["withdrawn"] = True
            row["withdrawn_reason"] = reason
        collision_key = (str(f.get("source_id") or ""), f["finding_id"])
        if collision_key in collided:
            row["id_collision"] = True
            row["ledger_held_back"] = ID_COLLISION_HELD_BACK % claims_at[collision_key]
            row["challenged"] = False
            routed.append(row)
            continue
        ledger = ledger_by_id.get(f["finding_id"])
        if ledger is not None:
            row["challenged"] = True
            row["ledger_verdict"] = ledger.get("verdict", "")
            row["ledger_method"] = ledger.get("method", "")
            # Who challenged it, not merely that something did: the deliverable
            # has to say which role overturned the verdict and what it proposed
            # instead, or "challenged" is a word the reader has to trust.
            dissent = [
                {"role": str(d.get("role") or "skeptic"),
                 "proposed_verdict": str(d.get("proposed_verdict") or ""),
                 "confidence": d.get("confidence")}
                for d in (ledger.get("dissent") or []) if isinstance(d, dict)
            ]
            if dissent:
                row["ledger_dissent"] = dissent
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
            "id_collision_rows": sum(1 for r in open_rows if r.get("id_collision")),
            # Counted over open_rows, exactly like id_collision_rows above: a
            # WITHDRAWN row is not held back from anybody, and the two figures
            # sitting side by side over different populations reads as a bug.
            "ledger_held_back": sum(1 for r in open_rows if r.get("ledger_held_back")
                                    and ledger_by_id.get(r["finding_id"]) is not None),
            "by_kind": by_kind,
            "by_severity": by_severity,
            "runs": sorted({r.get("run_id", "") for r in open_rows if r.get("run_id")}),
        },
        "schema": "routed-v2",
    }
    if conflicts:
        meta["source_sha_conflict"] = conflicts
    if coverage["collisions"]:
        meta["id_collisions"] = coverage["collisions"]
    if status == STATUS_UNCHALLENGED:
        meta["upstream"] = "REFUSED while UNCHALLENGED"
        meta["challenge_command"] = challenge_command(workspace, companion)
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


# --------------------------------------------------------------------------
# per-destination quote policy
# --------------------------------------------------------------------------

#: the verbatim sentence, up to the quote gate's bound
QUOTE_FULL = "full"
#: printed page + the first and last few words + the hash of the whole sentence
QUOTE_EXCERPT = "excerpt"
#: length and hash only -- what ``--redact-quotes`` writes into the export
QUOTE_HASH = "hash"
QUOTE_POLICIES = (QUOTE_FULL, QUOTE_EXCERPT, QUOTE_HASH)

#: gate/verify_findings.py's LONG_QUOTE_CHARS, spelled the same way on purpose:
#: no artifact quotes more than this.  It is NOT that module's MAX_QUOTE_CHARS,
#: which is the 3,000-character refusal; two modules disagreeing about what one
#: name bounds is how the next reader greps the wrong number.
LONG_QUOTE_CHARS = 600
#: words kept at each end of an excerpted quote
EXCERPT_WORDS = 6

#: The destinations a deliverable can be addressed to, and the quote policy each
#: one gets unless ``policy.destinations.<name>.quotes`` says otherwise.  The
#: default was per WORKSPACE (``redact_quotes_on_export``), which is the wrong
#: axis: the repo owner owns the book and needs the sentence, while a public
#: tracker must not carry it.  It is never inferred from a git remote -- a
#: private fork of a public repo and a public mirror of a private one both
#: exist, and that guess decides whether a copyrighted sentence is published.
DESTINATION_DEFAULTS = {
    "owner": QUOTE_FULL,      # the repository owner, privately
    "public": QUOTE_EXCERPT,  # an issue tracker anyone can read
}
#: the owner-message draft never leaves the workspace directory
DEFAULT_DESTINATION = "owner"
#: what ``cf route --github`` addresses unless told otherwise
GITHUB_DESTINATION = "public"


def quote_policy(policy: Mapping | None, destination: str = DEFAULT_DESTINATION) -> str:
    """The quote policy for *destination*: the workspace's, else the built-in default."""
    dest = str(destination or DEFAULT_DESTINATION)
    if dest not in DESTINATION_DEFAULTS:
        raise PolicyRefused(
            "unknown destination %r: this skill implements %s. Pass one of them with "
            "--destination." % (dest, ", ".join(sorted(DESTINATION_DEFAULTS)))
        )
    block = ((policy or {}).get("destinations") or {}).get(dest) or {}
    chosen = str((block or {}).get("quotes") or "") or DESTINATION_DEFAULTS[dest]
    if chosen not in QUOTE_POLICIES:
        raise PolicyRefused(
            "workspace.yaml policy.destinations.%s.quotes is %r, which is not one of %s. "
            "Fix the key, or name a policy on the command line with --quotes."
            % (dest, chosen, ", ".join(QUOTE_POLICIES))
        )
    return chosen


def render_quote(quote: Any, policy: str, *, page: Any = None) -> str:
    """The book sentence as *policy* allows it to be written down.

    ``full`` stops at :data:`LONG_QUOTE_CHARS`, the same bound the quote gate
    enforces on a finding, so no artifact this skill writes quotes more of a
    source than the gate already allowed into the corpus.
    """
    text = " ".join(str(quote or "").split())
    if not text:
        return ""
    where = "printed page %s" % page if page not in (None, "") else "printed page unknown"
    digest = compat.sha256_text(text.lower())
    if policy == QUOTE_FULL:
        if len(text) > LONG_QUOTE_CHARS:
            return '"%s…" (truncated at %d characters, the quote gate\'s bound)' % (
                text[:LONG_QUOTE_CHARS], LONG_QUOTE_CHARS)
        return '"%s"' % text
    if policy == QUOTE_EXCERPT:
        words = text.split(" ")
        if len(words) <= 2 * EXCERPT_WORDS:
            body = '"%s"' % text
        else:
            body = '"%s …" … "… %s"' % (" ".join(words[:EXCERPT_WORDS]),
                                        " ".join(words[-EXCERPT_WORDS:]))
        return "%s, %s (%d words, sha256 %s…)" % (body, where, len(words), digest[:12])
    return "quote withheld for this destination: %s, %d characters, sha256 %s…" % (
        where, len(text), digest[:12])


#: verdicts that may be reported to a repository owner. UNSURE is challenged
#: but undecided, so it is held back with the rest: the human-confirmation
#: policy is that a machine verdict the pipeline cannot defend does not travel.
REPORTABLE_VERDICTS = frozenset({"CONFIRMED_CONTENT", "ATTRIBUTION_GAP"})


def require_exportable(routed: dict, companion: str, *, workspace: str = "",
                       what: str = "an upstream report") -> dict:
    """The gate every artifact that leaves the workspace directory passes. Returns the meta.

    Refuses -- loudly -- while ``_meta.status`` is UNCHALLENGED: a first-pass
    finding is wrong about 40% of the time, and 32 unchallenged findings were
    held back from teorth for exactly this reason.  It also refuses a file in
    which one ``source_id`` carries more than one ``source_sha`` -- that means
    two editions were routed as if they were the same source, which is how
    batch 1's anchor findings ended up judged against the 2011 draft.  Rows
    from two DIFFERENT sources are fine: they say which is which.

    Shared by the JSONL export and by the issue bodies ``cf route --github``
    writes, because one of them passing the gate and the other not would make the
    gate a property of the format rather than of the findings.
    """
    meta = routed.get("meta") or {}
    if meta.get("status") != STATUS_CHALLENGED:
        raise UpstreamRefused(
            "refusing to emit %s for %s: status is %s "
            "(%d unchallenged row(s)). Challenge them first:\n    %s"
            % (
                what,
                companion,
                meta.get("status", "UNKNOWN"),
                (meta.get("counts") or {}).get("unchallenged", 0),
                meta.get("challenge_command") or challenge_command(workspace, companion),
            )
        )
    if meta.get("source_sha_conflict"):
        raise UpstreamRefused(
            "refusing to emit %s for %s: source(s) %s carry rows judged "
            "against more than one edition. Re-run the stages that produced the odd one."
            % (what, companion, ", ".join(meta["source_sha_conflict"]))
        )
    return meta


def emit_upstream(
    routed: dict,
    dest_dir: str,
    *,
    companion: str,
    workspace: str = "",
    redact_quotes: bool = False,
    open_only: bool = True,
) -> str:
    """Write the machine-readable upstream export for a routed file.

    Gated by :func:`require_exportable`, which is where the refusals and their
    reasons live.  ``redact_quotes`` keeps the workspace-wide
    ``redact_quotes_on_export`` behaviour for this file; the per-destination
    policy (:func:`quote_policy`) governs the human-readable drafts.
    """
    meta = require_exportable(routed, companion, workspace=workspace, what="an upstream report")
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


def _page_cell(row: dict) -> str:
    """The printed page, or what to do about its absence."""
    page = row.get("page")
    return str(page) if page not in (None, "") else "—"


def verdict_line(row: dict) -> str:
    """The adjudication outcome: the verdict, how it was reached, and who dissented."""
    if row.get("ledger_held_back"):
        return row["ledger_held_back"]
    verdict = str(row.get("ledger_verdict") or "")
    if not verdict:
        return "not challenged yet — this row may not be reported as a finding"
    out = "**%s**" % verdict
    method = str(row.get("ledger_method") or "")
    if method:
        out += " (%s)" % method
    dissent = row.get("ledger_dissent") or []
    if dissent:
        out += "; %d dissent(ing) vote(s): %s" % (
            len(dissent),
            ", ".join(
                "%s proposed %s%s" % (
                    d.get("role") or "skeptic",
                    d.get("proposed_verdict") or "another verdict",
                    "" if d.get("confidence") is None else " at %s" % d["confidence"])
                for d in dissent),
        )
    if row.get("contested"):
        out += "; **contested** (a skeptic overturned it at or above the threshold)"
    if row.get("resolution"):
        out += "; resolution: %s" % row["resolution"]
    return out


def finding_section(row: dict, *, quotes: str, heading: str = "##") -> list[str]:
    """One finding, with the evidence a maintainer needs to check it.

    Everything here is read off the routed row: the item and its printed page,
    the book sentence (as this destination's quote policy allows), the
    declaration with ``file:line`` and the statement head, the one-sentence
    difference, the witness, the adjudication outcome, and the finding id.  The
    old five-column table asked a maintainer to take all of that on trust.
    """
    out = [
        "%s %s — %s, %s" % (heading, row.get("item", ""), row.get("kind", ""),
                            row.get("severity", "") or "severity unrecorded"),
        "",
        "- Source: `%s` (%s…), printed page %s."
        % (row.get("source_id", ""), str(row.get("source_sha") or "")[:12], _page_cell(row)),
    ]
    quote = render_quote(row.get("book_quote"), quotes, page=row.get("page"))
    if quote:
        out.append("- The book says: %s" % quote)
    for site in row.get("decl_sites") or []:
        if site.get("found"):
            out.append("- `%s` — `%s:%s`" % (site.get("decl", ""), site.get("file", ""),
                                             site.get("line", "")))
            if site.get("statement"):
                out.append("  - `%s`" % site["statement"])
        else:
            out.append("- `%s` — not in this companion's statement table; re-run `cf extract`"
                       % site.get("decl", ""))
    if not row.get("decl_sites"):
        for decl in row.get("unfaithful_decls") or []:
            out.append("- `%s` — no file:line recorded; re-run `cf extract` then `cf route`" % decl)
    if row.get("concept"):
        out.append("- Concept: %s" % row["concept"])
    if row.get("reasoning"):
        out.append("- The difference: %s" % row["reasoning"])
    if row.get("witness"):
        out.append("- Witness: `%s`" % _one_line(row["witness"]))
    if row.get("suggested_fix"):
        out.append("- Suggested fix: %s" % row["suggested_fix"])
    out.append("- Verdict: %s" % verdict_line(row))
    out.append("- Finding id: `%s`" % row.get("finding_id", ""))
    out.append("")
    return out


def _one_line(value: Any) -> str:
    """A witness (a string or a small mapping) on one line."""
    if isinstance(value, Mapping):
        return "; ".join("%s=%s" % (k, " ".join(str(v).split())) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "; ".join(_one_line(v) for v in value)
    return " ".join(str(value).split())


def owner_message(routed: dict, companion: str, owner: str = "", *,
                  destination: str = DEFAULT_DESTINATION, quotes: str = "") -> str:
    """A DRAFT message for the repo owner. Nothing here sends anything.

    One SECTION per finding, not a row in a table: this file is the only part of
    the pipeline anyone outside the campaign ever sees, and a maintainer cannot
    act on an item label and a verdict.  ``quotes`` overrides the destination's
    policy; the default destination is ``owner``, which carries the sentence,
    because the repository owner owns the book and this draft stays inside the
    workspace directory until a human sends it.
    """
    meta = routed.get("meta") or {}
    counts = meta.get("counts") or {}
    rows = [r for r in routed["rows"] if not r.get("withdrawn")]
    policy = quotes or DESTINATION_DEFAULTS.get(destination, QUOTE_EXCERPT)
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
        "Quotes: %s (destination `%s`)." % (policy, destination),
        "",
    ]
    if meta.get("status") != STATUS_CHALLENGED:
        lines += [
            "**These rows have NOT been challenged and must not be sent as findings.**",
            "Run: `%s`" % (meta.get("challenge_command") or ""),
            "",
        ]
    if counts.get("id_collision_rows"):
        lines += [
            "%d row(s) are held back because two findings share one `finding_id`; they are "
            "listed below with the reason and cannot be adjudicated as they stand."
            % counts["id_collision_rows"],
            "",
        ]
    multi = len(meta.get("sources") or {}) > 1
    header = ["item", "kind", "severity", "page", "declaration", "verdict"]
    if multi:
        # Two sources: the item column alone is ambiguous, so name the source.
        header.insert(0, "source")
    lines += ["## Contents", "",
              "| " + " | ".join(header) + " |",
              "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        decls = ", ".join(r.get("unfaithful_decls") or [])
        cells = [r.get("item", ""), r.get("kind", ""), r.get("severity", ""), _page_cell(r),
                 "`%s`" % decls,
                 r.get("ledger_verdict") or ("held back" if r.get("ledger_held_back")
                                             else "(unchallenged)")]
        if multi:
            cells.insert(0, r.get("source_id", ""))
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines.append("")
    for r in rows:
        lines += finding_section(r, quotes=policy)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# `cf route --github`: one issue body per accepted finding, filed by a human
# --------------------------------------------------------------------------

#: the framing around the evidence; the evidence itself is :func:`finding_section`
ISSUE_TEMPLATE = "github-issue.md"
#: the label every issue this skill drafts carries, so a maintainer can filter them
ISSUE_LABEL = "faithfulness"

_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SLUG = re.compile(r"[^a-z0-9]+")


def template_path(name: str = ISSUE_TEMPLATE, explicit: str = "") -> str:
    """Where ``templates/<name>`` lives (the skill checkout, not the workspace)."""
    if explicit:
        return explicit
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "templates", name)


def render_template(text: str, values: Mapping[str, Any]) -> str:
    """Substitute ``{{token}}``; an absent or empty token renders as nothing.

    Deliberately unlike the handoff renderer, which turns a hole into
    ``TODO(...)``: an issue body is read by someone outside the campaign, and a
    section with nothing in it should be absent rather than advertised.
    """
    return _TOKEN.sub(lambda m: str(values.get(m.group(1), "") or ""), text)


def issue_slug(*parts: Any) -> str:
    """A file name from an item and a source id, safe to join onto a directory."""
    slug = _SLUG.sub("-", "-".join(str(p or "") for p in parts).lower()).strip("-")
    return slug[:120] or "finding"


def issue_title(rows: list[dict]) -> str:
    """A SUGGESTED title: the item, then the concept of the first finding."""
    item = str(rows[0].get("item") or "")
    concept = " ".join(str(rows[0].get("concept") or "").split())
    if len(rows) > 1:
        concept = "%d faithfulness findings" % len(rows)
    if not concept:
        concept = "%s finding" % (rows[0].get("kind") or "faithfulness")
    title = "%s: %s" % (item, concept)
    return title if len(title) <= 100 else title[:97].rstrip() + "…"


def issue_labels(rows: list[dict]) -> list[str]:
    """Suggested labels: the marker, each kind, and the highest severity present."""
    order = ["high", "medium", "low"]
    kinds = sorted({str(r.get("kind") or "") for r in rows if r.get("kind")})
    severities = [s for s in order if any(str(r.get("severity") or "") == s for r in rows)]
    labels = [ISSUE_LABEL] + ["kind:%s" % k for k in kinds]
    if severities:
        labels.append("severity:%s" % severities[0])
    return labels


def gh_create_command(body_file: str, title: str, labels: Iterable[str],
                      repo: str = "") -> str:
    """The exact ``gh`` command an operator runs. This module never runs it."""
    parts = ["gh", "issue", "create"]
    if repo:
        parts += ["--repo", repo]
    parts += ["--title", title, "--body-file", os.path.abspath(body_file)]
    for label in labels:
        parts += ["--label", label]
    return " ".join(shlex.quote(p) for p in parts)


def _issue_body(rows: list[dict], *, companion: str, repo: str, quotes: str,
                template: str = "") -> str:
    """Render one issue body from the template plus one section per finding.

    Deliberately carries NO absolute path: this file is drafted for a tracker
    anyone can read, and the auditor's own directory layout is not part of the
    finding.  The workspace-absolute paths live in ``index.json``, which stays in
    the workspace.
    """
    with open(template_path(explicit=template), "r", encoding="utf-8") as fh:
        text = fh.read()
    sources = sorted({(str(r.get("source_id") or ""), str(r.get("source_sha") or ""))
                      for r in rows})
    source_line = ", ".join("`%s` (sha256 %s…)" % (sid, sha[:12]) for sid, sha in sources)
    verify = ["```bash",
              "# from the audit workspace, with the `cf` shim on PATH",
              "cf route --companion %s --github" % companion,
              "```",
              "",
              "Every line above was read from that workspace's "
              "`ledger/routed/findings.%s.jsonl`." % companion]
    return render_template(text, {
        "repo_name": repo or companion,
        "source_line": source_line or "the source it was audited against",
        "findings": "\n".join("\n".join(finding_section(r, quotes=quotes)) for r in rows),
        "decision_note":
            "Each finding was produced by a model pass over the source and the Lean statements, "
            "then adjudicated by an independent pass with opposing skeptics. Only the verdicts "
            "%s are reported; everything else is held back, because a first-pass finding in this "
            "pipeline is wrong about 40%% of the time and calling a house convention a content bug "
            "is how it is wrong. The verdict and any dissent are printed with each finding above."
            % ", ".join("`%s`" % v for v in sorted(REPORTABLE_VERDICTS)),
        "verify_block": "\n".join(verify),
        "provenance":
            "Drafted by the companion-faithfulness audit (findings only: nothing was pushed and no "
            "pull request was opened). Quote policy for this destination: %s." % quotes,
    })


def github_issues(
    routed: dict,
    *,
    companion: str,
    repo: str = "",
    destination: str = GITHUB_DESTINATION,
    quotes: str = "",
    policy: Mapping | None = None,
    workspace: str = "",
    routed_file: str = "",
    template: str = "",
) -> dict:
    """One draft issue body per accepted finding, grouped by item. Calls nothing.

    Gated by :func:`require_exportable`, so an UNCHALLENGED file cannot be turned
    into issue bodies either.  "Accepted" is :data:`REPORTABLE_VERDICTS`, the same
    set the JSONL export uses.  Several findings on ONE item share one issue: a
    maintainer reading a tracker wants one thread per book item, not three.

    ``policy`` is the workspace's ``policy`` block, and it must be passed in:
    ``policy.destinations.public.quotes`` is the key
    ``templates/workspace.yaml.example`` annotates as "``cf route --github``
    issue bodies", and reading only the built-in default here made a workspace
    that asked for ``hash`` publish the excerpt -- the permissive direction, on
    the one artifact that can reach a tracker anyone can read.

    Returns ``{"index": {...}, "issues": [...]}``; the bodies are strings, and
    :func:`write_github_issues` is what puts them on disk.  Nothing here touches
    the network or invokes ``gh``: the index carries the exact command to run.
    """
    require_exportable(routed, companion, workspace=workspace,
                       what="GitHub issue bodies")
    policy_name = quotes or quote_policy(policy, destination)
    if policy_name not in QUOTE_POLICIES:
        raise PolicyRefused(
            "quote policy %r is not one of %s; name one with --quotes."
            % (policy_name, ", ".join(QUOTE_POLICIES)))
    rows = [r for r in routed["rows"]
            if not r.get("withdrawn")
            and str(r.get("ledger_verdict") or "") in REPORTABLE_VERDICTS]
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        grouped.setdefault((str(row.get("source_id") or ""), str(row.get("item") or "")),
                           []).append(row)
    issues = []
    for (source_id, item) in sorted(grouped):
        group = grouped[(source_id, item)]
        title = issue_title(group)
        labels = issue_labels(group)
        name = "%s.md" % issue_slug(source_id, item)
        severity = next((l.split(":", 1)[1] for l in labels if l.startswith("severity:")), "")
        issues.append({
            "file": name,
            "title": title,
            "labels": labels,
            "item": item,
            "source_id": source_id,
            "severity": severity,
            "kinds": sorted({str(r.get("kind") or "") for r in group if r.get("kind")}),
            "finding_ids": [str(r.get("finding_id") or "") for r in group],
            "verdicts": sorted({str(r.get("ledger_verdict") or "") for r in group}),
            "body": _issue_body(group, companion=companion, repo=repo, quotes=policy_name,
                                template=template),
        })
    index = {
        "schema": "routed-issues-v1",
        "companion": companion,
        "repo": repo,
        "destination": destination,
        "quotes": policy_name,
        "reportable_verdicts": sorted(REPORTABLE_VERDICTS),
        "routed_file": routed_file,
        "count": len(issues),
        "findings": sum(len(i["finding_ids"]) for i in issues),
        "issues": [{k: v for k, v in i.items() if k != "body"} for i in issues],
        "gh_note": ("`gh issue create` is printed, never run: this command opens a public thread "
                    "on someone else's repository, which is a human's decision."
                    + ("" if repo else " Pass --repo owner/name to name the repository, or run "
                                       "the command from a clone of it.")),
    }
    return {"index": index, "issues": issues}


def write_github_issues(routed: dict, dest_dir: str, **kwargs: Any) -> dict:
    """Write the issue bodies and ``index.json`` into *dest_dir*. Returns the index.

    Two item labels can slug to ONE file name (they differ only in punctuation,
    or both truncate at 120 characters).  Measured over the 347 baseline
    findings that happens 0 times, but the failure is silent when it does: one
    body overwrites the other while ``index.json`` still lists both, and the
    printed ``gh`` command for the loser points at the winner's evidence.  A
    repeat gets the finding id appended instead, and ``index.json`` records the
    name actually written.
    """
    built = github_issues(routed, **kwargs)
    index = built["index"]
    commands = []
    written: set[str] = set()
    for n, issue in enumerate(built["issues"]):
        if issue["file"] in written:
            suffix = (issue["finding_ids"] or [str(n)])[0][:8] or str(n)
            issue["file"] = "%s-%s.md" % (issue["file"][:-3], suffix)
            index["issues"][n]["file"] = issue["file"]
        written.add(issue["file"])
        path = os.path.join(dest_dir, issue["file"])
        compat.write_text(path, issue["body"])
        commands.append(gh_create_command(path, issue["title"], issue["labels"],
                                          index.get("repo") or ""))
    index["dir"] = os.path.abspath(dest_dir)
    index["commands"] = commands
    compat.write_json(os.path.join(dest_dir, "index.json"), index)
    return index


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
    destination: str = DEFAULT_DESTINATION,
    quotes: str = "",
) -> list[dict]:
    """Route every companion of a workspace; returns one report per companion.

    ``paths.root`` -- the RESOLVED workspace -- is what reaches ``route_repo``, not
    the argument: ``cf route`` may be run from a subdirectory with no
    ``--workspace``, and the literal ``<workspace>`` that used to stand in for it
    was written into the routed file and into the owner-message draft.
    """
    cfg = compat.load_workspace(workspace)
    paths = cfg.paths
    resolved = str(paths.root)
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
    pages = item_pages(paths, cfg.source_ids())
    policy = quotes or quote_policy(cfg.policy, destination)

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
            workspace=resolved,
            pages=pages,
            statements=workflow_args.load_statements(paths, name),
        )
        path = write_routed(paths, name, routed)
        if write_messages:
            compat.write_text(
                os.path.join(paths.routed, "message.%s.md" % name),
                owner_message(routed, name, destination=destination, quotes=policy),
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
        # ``policy`` travels with the report because ``cf route --github`` calls
        # :func:`write_github_issues` itself, after this function has returned:
        # without the block the issue bodies read the built-in default and a
        # workspace that asked for ``hash`` published the excerpt instead.
        reports.append({"companion": name, "file": path, "routed": routed,
                        "workspace": resolved, "routed_dir": str(paths.routed),
                        "quotes": policy, "destination": destination,
                        "policy": cfg.policy})
    return reports


def issues_dir(routed_dir: str, companion: str) -> str:
    """Where ``cf route --github`` writes by default: ``ledger/routed/github/<name>/``."""
    return os.path.join(routed_dir, "github", issue_slug(companion))


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
                    workspace=rep.get("workspace") or args.workspace,
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
