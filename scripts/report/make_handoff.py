"""Fill templates/handoff.md from computed artifacts only.

Ported from: the structure of /Users/jack/Desktop/notes/camera-ready-HANDOFF-2026-09-08.md
(sections 1-7) and tao-measure-lean/docs/HANDOFF.md ("four things I got wrong",
"verification that means something"), plus the tally recipe at
tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md:296-311.

The rule this module exists to enforce: **every number comes from a computed
artifact**, never from prose.  Its inputs are

  * ``reports/tally.<run>.json``            (harvest/tally.py)
  * ``ledger/{accepted,refuted}.<name>.jsonl`` and ``ledger/routed/findings.<name>.jsonl``
  * ``companions/<name>/state.json``
  * ``runs/<run-id>/run.yaml``
  * optional ``gh`` output saved to a JSON file -- this module never touches the network

A figure with no artifact behind it is rendered as ``TODO(<name>)`` next to the
command that would produce it.  The real project got this count wrong three
times and every error ran toward "cleaner", so a visible hole is the point.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
from typing import Any, Iterable

from ledger import compat
from ledger import validate_ledger as vl
from route import route as route_mod

TEMPLATE_NAME = "handoff.md"


def template_path(explicit: str = "") -> str:
    """Where templates/handoff.md lives."""
    if explicit:
        return explicit
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "templates", TEMPLATE_NAME)


def todo(name: str, how: str = "") -> str:
    """A visible hole, plus the command that fills it."""
    return "TODO(%s)%s" % (name, (" — `%s`" % how) if how else "")


# --------------------------------------------------------------------------
# readers (computed artifacts only)
# --------------------------------------------------------------------------


def load_tallies(paths: Any, explicit: Iterable[str] = ()) -> list[dict]:
    """Load ``reports/tally.<run>.json`` files written by harvest/tally.py.

    Shape (harvest/tally.py:tally): ``{generated_at, book_sha, rows,
    runs: {run_id: {dir, mode, files}}, repos: {repo: {rows, by_kind,
    by_severity, raw, collapsed, per_run}}}``.
    """
    files = [str(f) for f in explicit]
    if not files and os.path.isdir(paths.reports):
        files = [
            os.path.join(paths.reports, n)
            for n in sorted(os.listdir(paths.reports))
            if n.startswith("tally.") and n.endswith(".json")
        ]
    out = []
    for path in files:
        try:
            doc = compat.read_json(path)
        except Exception as exc:  # an unreadable tally is a hole, not a crash
            out.append({"file": path, "error": str(exc), "runs": {}, "repos": {}, "rows": None})
            continue
        if not isinstance(doc, dict):
            out.append({"file": path, "error": "not a JSON object", "runs": {}, "repos": {}, "rows": None})
            continue
        out.append(
            {
                "file": path,
                "generated_at": str(doc.get("generated_at") or ""),
                "book_sha": str(doc.get("book_sha") or ""),
                "rows": doc.get("rows"),
                "runs": doc.get("runs") or {},
                "repos": doc.get("repos") or {},
            }
        )
    return out


def load_runs(paths: Any) -> dict[str, dict]:
    """Read ``runs/<run-id>/run.yaml`` for the design table."""
    out: dict[str, dict] = {}
    root = paths.runs if hasattr(paths, "runs") else os.path.join(paths.root, "runs")
    if not os.path.isdir(str(root)):
        return out
    for name in sorted(os.listdir(str(root))):
        run_yaml = os.path.join(str(root), name, "run.yaml")
        if not os.path.exists(run_yaml):
            continue
        try:
            doc = compat.load_yaml(run_yaml) or {}
        except Exception:
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        doc["_file"] = run_yaml
        out[str(doc.get("run_id") or name)] = doc
    return out


def load_gh(files: Iterable[str]) -> list[dict]:
    """Read saved ``gh`` JSON output. This module never calls gh itself."""
    out = []
    for path in files:
        try:
            doc = compat.read_json(path)
        except Exception as exc:
            out.append({"file": path, "error": str(exc), "prs": [], "repo": "", "command": ""})
            continue
        if isinstance(doc, list):
            base = os.path.basename(path)
            repo = base[4:-5] if base.startswith("prs.") and base.endswith(".json") else base
            out.append({"file": path, "repo": repo, "command": "", "prs": doc})
        elif isinstance(doc, dict):
            out.append(
                {
                    "file": path,
                    "repo": str(doc.get("repo") or ""),
                    "command": str(doc.get("command") or ""),
                    "prs": doc.get("prs") or doc.get("pullRequests") or [],
                }
            )
    return out


def companion_facts(paths: Any, name: str, entry: dict | None = None) -> dict:
    """Everything the report knows about one companion, all of it computed.

    ``entry`` is the companion's campaign.yaml block, used only to fill the repo
    path when ``cf onboard`` has not written state.json yet.
    """
    state = compat.read_state(paths, name)
    entry = entry or {}
    if not state.get("repo") and entry.get("repo"):
        state = dict(state, repo=str(entry["repo"]))
    routed_path = paths.routed_findings(name)
    routed = route_mod.read_routed(routed_path) if os.path.exists(routed_path) else None
    ledger = vl.load_ledger_dir(paths, name)
    accepted = ledger["rows"]["accepted"]
    refuted = ledger["rows"]["refuted"]
    by_verdict: dict[str, int] = {}
    for row in accepted + refuted:
        key = str(row.get("verdict") or "")
        by_verdict[key] = by_verdict.get(key, 0) + 1
    return {
        "name": name,
        "state": state,
        "routed_file": routed_path if routed else "",
        "routed_meta": (routed or {}).get("meta", {}),
        "routed_rows": (routed or {}).get("rows", []),
        "accepted": accepted,
        "refuted": refuted,
        "by_verdict": by_verdict,
        "contested": sum(1 for r in accepted + refuted if r.get("contested")),
        "open_rows": [r for r in accepted if str(r.get("verdict")) in compat.OPEN_VERDICTS],
    }


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_(nothing computed yet)_"
    out = ["| %s |" % " | ".join(header), "|%s|" % "|".join("---" for _ in header)]
    for row in rows:
        out.append("| %s |" % " | ".join(_cell(c) for c in row))
    return "\n".join(out)


def render_state_table(facts: list[dict]) -> str:
    """Section 1: one line per companion, every cell from state.json or routed _meta."""
    rows = []
    for f in facts:
        state = f["state"] or {}
        counts = (f["routed_meta"] or {}).get("counts") or {}
        extract = state.get("extract") or {}
        rows.append(
            [
                "`%s`" % f["name"],
                state.get("repo") or todo("repo", "cf onboard"),
                (state.get("commit") or "")[:12] or todo("commit", "cf extract"),
                extract.get("items") or todo("items", "cf extract"),
                counts.get("rows", todo("routed rows", "cf route")),
                counts.get("items", ""),
                (f["routed_meta"] or {}).get("status") or todo("status", "cf route"),
                len(f["open_rows"]),
                f["contested"],
            ]
        )
    return _table(
        ["companion", "repo", "commit", "items", "routed rows", "routed items",
         "status", "open ledger rows", "contested"],
        rows,
    )


def render_runs_table(runs: dict[str, dict], tallies: list[dict]) -> str:
    """Section 2: one line per run, joining run.yaml with that run's tally."""
    seen: dict[str, dict] = {}
    for tally in tallies:
        for run_id, info in (tally.get("runs") or {}).items():
            entry = seen.setdefault(str(run_id), {})
            entry.setdefault("mode", info.get("mode"))
            entry.setdefault("files", info.get("files"))
            entry["tally_file"] = tally["file"]
    for run_id in runs:
        seen.setdefault(run_id, {})
    rows = []
    for run_id in sorted(seen):
        info = seen[run_id]
        cfg = runs.get(run_id) or {}
        models = cfg.get("models") if isinstance(cfg.get("models"), dict) else None
        rows.append(
            [
                "`%s`" % run_id,
                cfg.get("mode") or info.get("mode") or todo("mode", "cf plan-run"),
                cfg.get("model") or (models or {}).get("worker") or todo("model", "cf plan-run"),
                cfg.get("book_access") or todo("book access", "cf plan-run"),
                cfg.get("host") or "local",
                cfg.get("tasks") or cfg.get("task_count") or todo("tasks", "cf plan-run"),
                info.get("files", todo("finding files", "cf harvest")),
            ]
        )
    return _table(["run", "mode", "model", "book access", "host", "tasks", "finding files"], rows)


def _classes(entry: dict) -> str:
    raw = entry.get("raw") or {}
    classes = raw.get("classes") or {}
    return ", ".join("%s %s" % (v, k) for k, v in sorted(classes.items()) if v)


def render_findings_tables(facts: list[dict], tallies: list[dict]) -> str:
    """Section 3: per companion, the tally rows and the ledger's open rows."""
    blocks: list[str] = []
    for f in facts:
        name = f["name"]
        blocks += ["### `%s`" % name, ""]
        per_run_rows: list[list[Any]] = []
        totals: list[list[Any]] = []
        for tally in tallies:
            entry = (tally.get("repos") or {}).get(name)
            if not isinstance(entry, dict):
                continue
            for run_id, counts in sorted((entry.get("per_run") or {}).items()):
                if not counts.get("rows"):
                    continue
                per_run_rows.append(
                    ["`%s`" % run_id, counts.get("rows"), counts.get("items"),
                     counts.get("collapsed_items")]
                )
            raw = entry.get("raw") or {}
            totals.append(
                [
                    os.path.basename(tally["file"]),
                    entry.get("rows"),
                    raw.get("items"),
                    raw.get("substantive"),
                    raw.get("high"),
                    ", ".join("%s %s" % (v, k) for k, v in sorted((entry.get("by_kind") or {}).items())),
                    _classes(entry),
                ]
            )
        if per_run_rows:
            blocks += [_table(["run", "rows", "raw items", "book items"], per_run_rows), ""]
        if totals:
            blocks += [
                _table(["tally", "rows", "items", "substantive", "high", "by kind", "classes"], totals),
                "",
            ]
        if not per_run_rows and not totals:
            blocks += [todo("tally for %s" % name, "cf harvest --tally"), ""]

        meta = f["routed_meta"] or {}
        if meta:
            counts = meta.get("counts") or {}
            blocks += [
                "Routed: **%s** — %s row(s) over %s item(s); %s challenged, %s unchallenged, "
                "%s withdrawn. Rule: %s."
                % (
                    meta.get("status", ""),
                    counts.get("rows", "?"),
                    counts.get("items", "?"),
                    counts.get("challenged", "?"),
                    counts.get("unchallenged", "?"),
                    counts.get("withdrawn", "?"),
                    meta.get("rule", ""),
                ),
                "",
            ]
        if f["by_verdict"]:
            blocks += [
                "Ledger: "
                + ", ".join("%d %s" % (n, v) for v, n in sorted(f["by_verdict"].items()) if v)
                + ("; %d contested." % f["contested"] if f["contested"] else "."),
                "",
            ]
        if f["open_rows"]:
            blocks += [
                "Open rows:",
                "",
                _table(
                    ["item", "verdict", "severity", "declaration", "resolution"],
                    [
                        [
                            r.get("item", ""),
                            r.get("verdict", ""),
                            r.get("severity", ""),
                            "`%s`" % ", ".join(r.get("unfaithful_decls") or []),
                            r.get("resolution", "open"),
                        ]
                        for r in f["open_rows"]
                    ],
                ),
                "",
            ]
    return "\n".join(blocks) if blocks else todo("findings", "cf harvest --tally && cf route")


def render_owner_actions(facts: list[dict], gh: list[dict]) -> str:
    """Section 4: what a human must decide, derived from the ledger and gh output."""
    lines = []
    for f in facts:
        meta = f["routed_meta"] or {}
        if meta.get("status") == route_mod.STATUS_UNCHALLENGED:
            counts = meta.get("counts") or {}
            lines.append(
                "- `%s`: **%s unchallenged row(s)** — nothing may be reported upstream until they are "
                "challenged; `route` refuses the export. Run:\n      %s"
                % (f["name"], counts.get("unchallenged", "?"), meta.get("challenge_command", ""))
            )
        for row in [r for r in f["accepted"] + f["refuted"] if r.get("contested")]:
            lines.append(
                "- `%s`: %s is **contested** (a skeptic overturned the verdict at or above the "
                "threshold); the verdict on file is %s."
                % (f["name"], row.get("item", ""), row.get("verdict", ""))
            )
        for row in [r for r in f["accepted"] if str(r.get("verdict")) == "UNSURE"]:
            lines.append(
                "- `%s`: %s is UNSURE — a human decides, or the row says what would decide it."
                % (f["name"], row.get("item", ""))
            )
        for row in f["open_rows"]:
            if not row.get("resolution"):
                lines.append(
                    "- `%s`: %s (%s, %s) is open and unrouted to any action — the owner decides "
                    "whether it becomes a fix." % (f["name"], row.get("item", ""),
                                                   row.get("verdict", ""), row.get("severity", ""))
                )
    for bundle in gh:
        for pr in bundle.get("prs") or []:
            if str(pr.get("state", "")).upper() == "OPEN":
                lines.append(
                    "- %s PR #%s %s — %s (from `%s`)"
                    % (bundle.get("repo", ""), pr.get("number", "?"), pr.get("title", ""),
                       pr.get("url", ""), bundle.get("command") or bundle.get("file", ""))
                )
    if not lines:
        lines.append(
            "- Nothing is waiting on a human **that this report can prove**. "
            + todo("owner actions", "cf route && cf report")
        )
    return "\n".join(lines)


def render_paths_list(paths: Any, facts: list[dict]) -> str:
    """Section 6."""
    lines = [
        "- Campaign directory (private git repo, no remote): `%s`" % paths.root,
        "- Corpus, append-only per run: `%s`" % paths.corpus(),
        "- Ledger: `%s`" % paths.ledger,
        "- Routed files and owner-message drafts: `%s`" % paths.routed,
        "- Reports: `%s`" % paths.reports,
        "- Book text, page images and crops are gitignored and never leave the campaign directory.",
    ]
    for f in facts:
        repo = (f["state"] or {}).get("repo")
        if repo:
            lines.append("- Companion `%s` (read-only to this pipeline): `%s`" % (f["name"], repo))
    return "\n".join(lines)


DEFAULT_TRAPS = [
    "Two PDF editions: a page anchor is only meaningful against the pinned sha256. A finding judged "
    "against another printing is not a finding.",
    "The quote gate runs OUT OF TREE. A gate living inside the tree a worker can edit proves nothing.",
    "Counts come from the corpus, and the corpus can be short: check the harvest receipt (one run "
    "landed 762 of 763 files, and the missing one was a real CONTENT finding).",
    "A first-pass finding is wrong about 40% of the time, always by calling a house convention a "
    "content bug. Nothing goes upstream unchallenged.",
    "PAGE_ANCHOR is mechanical-only: 22 of 40 differential-flagged items were pagination.",
]


def render_traps(extra: Iterable[str] = ()) -> str:
    return "\n".join("- %s" % t for t in list(DEFAULT_TRAPS) + list(extra))


def render_verification(paths: Any, facts: list[dict], tallies: list[dict], gh: list[dict]) -> str:
    """Section 8: the commands that recompute every number above."""
    lines = ["```bash", "# routing counts (sections 1 and 3)",
             "python3 scripts/cf.py route --campaign %s" % paths.root,
             "# ledger counts, and that every row answers a real finding",
             "python3 scripts/cf.py adjudicate --campaign %s --validate" % paths.root,
             "# the tally every finding number comes from",
             "python3 scripts/cf.py harvest --campaign %s --tally" % paths.root]
    for tally in tallies:
        lines.append("#   -> %s" % tally["file"])
    if not tallies:
        lines.append("#   -> %s" % todo("tally files"))
    for bundle in gh:
        if bundle.get("command"):
            lines.append("# PR state: %s" % bundle["command"])
    lines.append("```")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


def build_context(
    campaign: str,
    *,
    tally_files: Iterable[str] = (),
    gh_files: Iterable[str] = (),
    date: str = "",
    tldr: str = "",
    wrong_section: str = "",
    traps: Iterable[str] = (),
) -> dict:
    """Compute every substitution for templates/handoff.md."""
    cfg = compat.load_campaign(campaign)
    paths = cfg.paths
    names = cfg.companion_names()
    facts = [companion_facts(paths, n, cfg.companion(n)) for n in names]
    tallies = load_tallies(paths, tally_files)
    runs = load_runs(paths)
    gh = load_gh(gh_files)
    book_sha = cfg.book_sha

    unchallenged = [
        f for f in facts
        if (f["routed_meta"] or {}).get("status") == route_mod.STATUS_UNCHALLENGED
    ]
    status_note = (
        "**%d companion(s) still carry unchallenged rows**; `route` refuses to emit their upstream "
        "reports, and this handoff may not be used as one." % len(unchallenged)
        if unchallenged
        else "Every routed file is CHALLENGED: each row carries a ledger verdict."
    )
    open_total = sum(len(f["open_rows"]) for f in facts)
    default_tldr = (
        "%d companion(s) audited against `%s`; %d open ledger row(s) across %d routed file(s); %s"
        % (
            len(facts),
            (book_sha or "")[:12] or todo("book_sha", "cf book"),
            open_total,
            sum(1 for f in facts if f["routed_meta"]),
            "no upstream report may be produced yet." if unchallenged
            else "every routed file is challenged.",
        )
    )
    return {
        "campaign": cfg.title or os.path.basename(paths.root),
        "date": date or _dt.date.today().isoformat(),
        "book_id": cfg.book_id or todo("book_id", "cf book"),
        "book_sha": book_sha or todo("book_sha", "cf book"),
        "book_note": (", printed page = PDF page - %d" % cfg.offset) if cfg.offset else "",
        "tldr": tldr or default_tldr,
        "state_table": render_state_table(facts),
        "status_note": status_note,
        "runs_table": render_runs_table(runs, tallies),
        "yield_note": (
            "Every cell above is `run.yaml` or a tally file; no number here was typed by hand."
            if tallies else todo("yield table", "cf harvest --tally")
        ),
        "findings_tables": render_findings_tables(facts, tallies),
        "owner_actions": render_owner_actions(facts, gh),
        "wrong_section": wrong_section or (
            "_%s — this section is written by the person who ran the pass. It is the transferable "
            "part of the report and no script can fill it._" % todo("what went wrong")
        ),
        "paths_list": render_paths_list(paths, facts),
        "traps_list": render_traps(traps),
        "verification_commands": render_verification(paths, facts, tallies, gh),
        "provenance": "_Generated by `cf report` from %d tally file(s), %d run.yaml file(s) and %d "
        "saved gh output file(s). Figures with no artifact behind them are marked TODO._"
        % (len(tallies), len(runs), len(gh)),
        "_facts": facts,
        "_tallies": tallies,
        "_paths": paths,
        "_campaign": cfg,
    }


def render(context: dict, template: str = "") -> str:
    """Substitute ``{{token}}`` placeholders; an unfilled token becomes TODO()."""
    with open(template_path(template), "r", encoding="utf-8") as fh:
        text = fh.read()
    out: list[str] = []
    i = 0
    while True:
        start = text.find("{{", i)
        if start < 0:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = text.find("}}", start)
        if end < 0:
            out.append(text[start:])
            break
        key = text[start + 2:end].strip()
        value = context.get(key)
        out.append(str(value) if value not in (None, "") else todo(key))
        i = end + 2
    return "".join(out)


def make_handoff(
    campaign: str,
    *,
    out_path: str = "",
    tally_files: Iterable[str] = (),
    gh_files: Iterable[str] = (),
    date: str = "",
    tldr: str = "",
    wrong_section: str = "",
    template: str = "",
) -> dict:
    """Render ``reports/handoff.<date>.md``. Returns {path, todos, context, text}."""
    ctx = build_context(
        campaign,
        tally_files=tally_files,
        gh_files=gh_files,
        date=date,
        tldr=tldr,
        wrong_section=wrong_section,
    )
    text = render(ctx, template)
    paths = ctx["_paths"]
    path = out_path or os.path.join(paths.reports, "handoff.%s.md" % ctx["date"])
    compat.write_text(path, text)
    todos = sorted(set(re.findall(r"TODO\([^)]*\)", text)))
    return {"path": path, "todos": todos, "context": ctx, "text": text}


def main(argv: list[str] | None = None) -> int:
    """CLI mirror of ``cf report`` (see scripts/cfcmd/report.py)."""
    ap = argparse.ArgumentParser(description="Render the campaign handoff from computed artifacts.")
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--tally", action="append", default=[])
    ap.add_argument("--gh-json", action="append", default=[])
    ap.add_argument("--out", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--tldr", default="")
    args = ap.parse_args(argv)
    report = make_handoff(
        args.campaign,
        out_path=args.out,
        tally_files=args.tally,
        gh_files=args.gh_json,
        date=args.date,
        tldr=args.tldr,
    )
    print("wrote %s (%d TODO placeholder(s))" % (report["path"], len(report["todos"])))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
