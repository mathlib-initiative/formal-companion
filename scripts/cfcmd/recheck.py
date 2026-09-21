"""``cf recheck`` -- are the pinned findings still claims about today's code?

New code (the pin recorded by ``scripts/recheck/pin.py``; design/
multi-source-model.md S6.2).

A routed finding is a claim about a specific Lean statement at a specific
revision.  Without re-checking it, six months later nobody can tell a live
finding from one already fixed upstream, and the ledger's open set only ever
grows.  This command re-extracts the companion's statements at its CURRENT
commit, compares each open finding's pinned declaration fingerprints with what
is there now, and labels every row ``unchanged`` / ``changed`` / ``gone`` /
``unpinned``.

It changes no verdict.  A ``changed`` row is marked ``needs_rereview``, because a
statement that moved may have been fixed or made worse and only a reader can say
which.  It calls no model, it reads the companion repository only (through
``cflib.gitsafe``, never writing to it), and re-running it rewrites the same
answer: the only thing it mutates is a ``recheck`` block on the ledger rows and
``reports/recheck.<companion>.json``.

    python3 scripts/cf.py recheck                    # every companion
    python3 scripts/cf.py recheck --companion ours    # one
    python3 scripts/cf.py recheck --json
"""
from __future__ import annotations

from pathlib import Path

from cflib import log
from cflib import state as state_mod
from cflib.adapters_api import load as load_adapter
from cflib.errors import CfError
from cflib.jsonio import read_json, read_jsonl, write_json, write_jsonl
from cflib.workspace import from_args

from recheck import pin as pin_mod
from recheck import recheck as recheck_mod

NAME = "recheck"
HELP = "re-check every open finding against the companion's current statements"


def register(subparsers) -> None:
    p = subparsers.add_parser(NAME, help=HELP)
    p.add_argument("--companion", action="append", default=[],
                   help="re-check one companion (repeatable; default: all of them)")
    p.add_argument("--from-stored", action="store_true",
                   help="compare against companions/<name>/statements.jsonl as it stands "
                        "instead of re-extracting (faster; only honest when `cf extract` "
                        "has been run since the companion last moved)")
    p.add_argument("--no-write", action="store_true",
                   help="print the re-check without touching the ledger rows or the report")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")


def run(args) -> int:
    workspace = from_args(args)
    names = args.companion or workspace.companion_names()
    if not names:
        raise CfError("this workspace has no companions to re-check",
                      hint="run `cf onboard <repo>` first")
    reports = []
    needs = 0
    for name in names:
        report = _recheck_one(workspace, name, args)
        reports.append(report)
        needs += sum(1 for e in report["entries"] if e["needs_rereview"])
        log.info(recheck_mod.render(report))
    if args.json:
        import json
        log.info(json.dumps(reports if len(reports) > 1 else reports[0],
                            indent=1, sort_keys=True))
    if needs:
        log.warn(f"{needs} finding(s) need re-review: the statement they are about has "
                 f"changed or is gone. `cf adjudicate --companion <name>` is what settles "
                 f"them; no verdict was altered here.")
    return 0


def _current_statements(workspace, name: str, args) -> tuple[dict, int]:
    """``({decl: statement}, declarations)`` for the companion as it stands NOW.

    Re-extracting is the point: a re-check against the stored table would answer
    the question "has ``cf extract`` been run", not "has the companion moved".
    ``--from-stored`` is the escape for a workspace whose table is known current,
    and it is named in the report so the answer can never be mistaken for the
    fuller one.
    """
    stored = Path(workspace.paths.statements_jsonl(name))
    if args.from_stored:
        if not stored.exists():
            raise CfError(f"{name}: {stored} does not exist, so there is nothing to "
                          f"compare against",
                          hint=f"run `cf extract --companion {name}`, or drop --from-stored "
                               f"to re-extract here")
        rows = read_jsonl(stored, skip_meta=True)
    else:
        adapter_yaml = Path(workspace.paths.adapter_yaml(name))
        if not adapter_yaml.exists():
            raise CfError(f"{name}: {adapter_yaml} does not exist, so its statements cannot "
                          f"be re-extracted",
                          hint=f"run `cf onboard <repo> --name {name}`, or pass --from-stored "
                               f"to compare against the table already on disk")
        adapter = load_adapter(adapter_yaml, workspace=workspace)
        repo = adapter.resolve_repo()
        shas = {sid: workspace.source(sid).sha256
                for sid in (workspace.companion(name).sources or ())}
        rows = list(adapter.extract(repo, sources=shas))
    current: dict[str, str] = {}
    for row in rows:
        decl = str(row.get("decl") or "").strip()
        if decl and decl not in current:
            current[decl] = str(row.get("statement") or "")
    return current, len(current)


def _open_rows(paths, name: str) -> list[dict]:
    """The rows a re-check is about: open ledger rows, plus the routed file.

    A closed row is not re-checked -- a refutation does not expire -- and the
    routed file is included because that is the artifact a maintainer was sent,
    so "is what I sent still true" has to be answerable from it.  Rows are keyed
    by ``finding_id`` so a finding that is both accepted and routed is one entry.
    """
    from ledger import compat as ledger_compat

    out: dict[str, dict] = {}
    for path in (Path(paths.accepted_jsonl(name)), Path(paths.routed_jsonl(name))):
        if not path.exists():
            continue
        for row in read_jsonl(path, skip_meta=True):
            verdict = str(row.get("verdict") or "").strip()
            if verdict and verdict in ledger_compat.CLOSED_VERDICTS:
                continue
            out.setdefault(str(row.get("finding_id") or path.name), dict(row))
    return [out[k] for k in sorted(out)]


def _pin_docs(paths, rows) -> dict:
    """``{run_id: pin document}`` for the runs the rows came from."""
    docs: dict[str, dict] = {}
    for row in rows:
        run_id = str(row.get("run_id") or "")
        if not run_id or run_id in docs:
            continue
        try:
            path = Path(paths.pin_json(run_id))
        except CfError:
            docs[run_id] = {}
            continue
        loaded = read_json(path, default=None) if path.exists() else None
        docs[run_id] = loaded if isinstance(loaded, dict) else {}
    return docs


def _recheck_one(workspace, name: str, args) -> dict:
    paths = workspace.paths
    current, declarations = _current_statements(workspace, name, args)
    pin = pin_mod.repo_pin(getattr(workspace.companions.get(name), "repo", None))
    rows = _open_rows(paths, name)
    docs = _pin_docs(paths, rows)
    # The table the fingerprints were taken over, when it is still the one on
    # disk: that is what lets a `changed` row print the old statement, not just a
    # pair of hashes.
    stored = {}
    if declarations and not args.from_stored:
        stored = {str(r.get("decl") or ""): str(r.get("statement") or "")
                  for r in read_jsonl(paths.statements_jsonl(name), skip_meta=True)} \
            if Path(paths.statements_jsonl(name)).exists() else {}
    entries = [
        recheck_mod.classify_row(row, current=current, companion=name,
                                 pin_doc=docs.get(str(row.get("run_id") or "")),
                                 pinned_statements=stored)
        for row in rows
    ]
    report = {
        "generated_at": state_mod.now(),
        "companion": name,
        "current_commit": pin["repo_commit"] or "",
        "dirty": pin["dirty"],
        "compared_against": "statements.jsonl" if args.from_stored else "a fresh extraction",
        "declarations": declarations,
        "pinned_commits": sorted({e["pinned_commit"] for e in entries if e["pinned_commit"]}),
        "counts": recheck_mod.counts(entries),
        "entries": entries,
    }
    if not args.no_write:
        report["files"] = _write(workspace, name, report)
    return report


def _write(workspace, name: str, report: dict) -> list[str]:
    """Write the report and stamp a ``recheck`` block onto the ledger rows.

    Only the block is written: every other field of every row is copied through
    byte for byte, so a re-check can be re-run over an adjudicated ledger as
    often as anyone likes without moving a verdict, a severity or a resolution.
    """
    paths = workspace.paths
    out = Path(paths.recheck_json(name))
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, report, indent=1, sort_keys=True)
    written = [str(out)]
    by_id = {e["finding_id"]: e for e in report["entries"] if e["finding_id"]}
    for path in (Path(paths.accepted_jsonl(name)), Path(paths.refuted_jsonl(name))):
        if not path.exists() or not by_id:
            continue
        raw = read_jsonl(path)
        is_meta = lambda r: isinstance(r, dict) and len(r) == 1 and "_meta" in r
        meta = next((r["_meta"] for r in raw if is_meta(r)), None)
        rows = [r for r in raw if not is_meta(r)]
        touched = False
        for row in rows:
            entry = by_id.get(str(row.get("finding_id") or ""))
            if entry is None:
                continue
            block = recheck_mod.block(entry, at=report["generated_at"],
                                      current_commit=report["current_commit"])
            # the timestamp moves on every run; comparing without it is what makes
            # the write idempotent for a ledger nothing has changed under
            if {k: v for k, v in (row.get("recheck") or {}).items() if k != "at"} != \
                    {k: v for k, v in block.items() if k != "at"}:
                row["recheck"] = block
                touched = True
        if touched:
            write_jsonl(path, rows, meta=meta)
            written.append(str(path))
    with state_mod.update(workspace, name) as st:
        state_mod.set_section(st, "recheck", {
            "at": report["generated_at"],
            "commit": report["current_commit"],
            **{k: v for k, v in report["counts"].items()},
        })
    return written
