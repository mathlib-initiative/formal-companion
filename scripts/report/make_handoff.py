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

    Shape (harvest/tally.py:tally): ``{generated_at, sources, rows,
    runs: {run_id: {dir, mode, files}}, groups: {"<source_id>::<repo>": {...}},
    repos: {repo: {rows, by_kind, by_severity, raw, collapsed, per_run,
    sources}}}``.
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
            out.append({"file": path, "error": str(exc), "runs": {}, "repos": {},
                        "groups": {}, "sources": {}, "rows": None})
            continue
        if not isinstance(doc, dict):
            out.append({"file": path, "error": "not a JSON object", "runs": {}, "repos": {},
                        "groups": {}, "sources": {}, "rows": None})
            continue
        out.append(
            {
                "file": path,
                "generated_at": str(doc.get("generated_at") or ""),
                "sources": doc.get("sources") or {},
                "rows": doc.get("rows"),
                "runs": doc.get("runs") or {},
                "groups": doc.get("groups") or {},
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


def load_receipts(paths: Any) -> dict[str, dict]:
    """Read ``corpus/<run-id>/RECEIPT.json`` for runs that have no ``run.yaml``.

    An IMPORTED corpus never gets a ``run.yaml`` -- ``cf plan-run`` did not plan
    it -- but its receipt records the edition every document was judged against.
    Without this reader the runs table printed ``TODO(source)`` for a run whose
    source is written down two directories away.
    """
    out: dict[str, dict] = {}
    root = (paths.corpus_dir if hasattr(paths, "corpus_dir")
            else os.path.join(str(getattr(paths, "root", paths)), "corpus"))
    if not os.path.isdir(str(root)):
        return out
    for name in sorted(os.listdir(str(root))):
        receipt = os.path.join(str(root), name, "RECEIPT.json")
        if not os.path.exists(receipt):
            continue
        try:
            doc = compat.read_json(receipt)
        except Exception:
            continue
        if isinstance(doc, dict):
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

    ``entry`` is the companion's workspace.yaml block, used only to fill the repo
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


def _host_name(cfg: dict) -> str:
    """`cf plan-run` writes ``host: {name, kind, remote_root, ssh}``, not a string.

    Rendering the mapping put a Python dict repr in the handoff table.
    """
    host = cfg.get("host")
    if isinstance(host, dict):
        return str(host.get("name") or host.get("ssh") or "local")
    return str(host or "local")


def _source_access(cfg: dict) -> str:
    """What the workers could read of the source (run.yaml ``source_access``)."""
    if cfg.get("source_access"):
        return str(cfg["source_access"])
    book = cfg.get("book")
    if isinstance(book, dict) and book.get("access"):
        return str(book["access"])
    return str(cfg.get("book_access") or "")


def _run_sources(cfg: dict) -> str:
    """The source ids a run.yaml names, for the runs table."""
    value = cfg.get("sources")
    ids: list[str] = []
    if isinstance(value, dict):
        ids = [str(k) for k in value]
    elif isinstance(value, (list, tuple)):
        for entry in value:
            ids.append(str(entry.get("id") or entry.get("source_id") or "")
                       if isinstance(entry, dict) else str(entry))
    return ", ".join("`%s`" % i for i in ids if i)


def _task_count(cfg: dict):
    """``tasks`` is a mapping whose ``count`` is the number the table wants."""
    tasks = cfg.get("tasks")
    if isinstance(tasks, dict):
        return tasks.get("count")
    return tasks or cfg.get("task_count")


def _receipt_sources(receipt: dict) -> str:
    """The source ids a corpus RECEIPT.json names, for the runs table.

    ``sources`` is a {id: sha} map.  A list of bare ids is a receipt written
    before that shape settled; read it rather than printing a TODO next to a
    fact that is on disk.
    """
    value = receipt.get("sources")
    ids: list[str] = []
    if isinstance(value, dict):
        ids = [str(k) for k in sorted(value)]
    elif isinstance(value, (list, tuple)):
        ids = [str(v) for v in value]
    if not ids and receipt.get("source_id"):
        ids = [str(receipt["source_id"])]
    return ", ".join("`%s`" % i for i in ids if i)


def render_runs_table(runs: dict[str, dict], tallies: list[dict],
                      receipts: dict[str, dict] | None = None) -> str:
    """Section 2: one line per run, joining run.yaml and RECEIPT.json with the tally.

    A planned run has a ``run.yaml`` and every cell is filled from it.  An
    IMPORTED corpus has no run.yaml and never will: the model, the task count
    and the source access of a historical v1 run are not recorded anywhere, so
    those cells read ``--`` rather than a TODO pointing at ``cf plan-run``,
    which would never fill them.  The edition IS recorded, in the receipt.
    """
    receipts = receipts or {}
    seen: dict[str, dict] = {}
    for tally in tallies:
        for run_id, info in (tally.get("runs") or {}).items():
            entry = seen.setdefault(str(run_id), {})
            entry.setdefault("mode", info.get("mode"))
            entry.setdefault("files", info.get("files"))
            entry["tally_file"] = tally["file"]
    for run_id in runs:
        seen.setdefault(run_id, {})
    for run_id in receipts:
        seen.setdefault(run_id, {})
    rows = []
    for run_id in sorted(seen):
        info = seen[run_id]
        cfg = runs.get(run_id) or {}
        receipt = receipts.get(run_id) or {}
        # A run with a receipt but no run.yaml was never planned: an imported v1
        # corpus, or one `cf pair`/`cf detect-proxies` computed mechanically.
        # Neither records a model, a task count or a source access, and no
        # `cf plan-run` will ever fill them in, so they read `--`, not TODO.
        unplanned = bool(receipt) and not cfg
        gap = (lambda name, how: "--") if unplanned else todo
        models = cfg.get("models") if isinstance(cfg.get("models"), dict) else None
        mode = (cfg.get("mode") or info.get("mode")
                or (receipt.get("source") or {}).get("mode")
                or receipt.get("mode"))
        rows.append(
            [
                "`%s`" % run_id,
                mode or todo("mode", "cf plan-run"),
                cfg.get("model") or (models or {}).get("worker")
                or gap("model", "cf plan-run"),
                _run_sources(cfg) or _receipt_sources(receipt)
                or todo("source", "cf plan-run"),
                _source_access(cfg) or gap("source access", "cf plan-run"),
                _host_name(cfg),
                _task_count(cfg) or gap("tasks", "cf plan-run"),
                info.get("files", (receipt.get("actual") or {}).get("count")
                         or todo("finding files", "cf harvest")),
            ]
        )
    return _table(["run", "mode", "model", "source", "source access", "host", "tasks",
                   "finding files"], rows)


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
        # A run is counted by every tally file that covers it -- `cf harvest` and
        # `cf detect-proxies` write reports/tally.<run>.json, `cf tally` writes
        # tally.all.json over every run -- so the same (source, run) arrives more
        # than once. It is ONE run and gets ONE row; two tallies that disagree
        # about it mean one of them is stale, which is said out loud below rather
        # than printed as two rows the reader has to reconcile.
        seen_runs: dict[tuple[str, str], tuple[list[Any], str]] = {}
        stale: list[str] = []
        for tally in tallies:
            entry = (tally.get("repos") or {}).get(name)
            if not isinstance(entry, dict):
                continue
            # One totals row per (tally, source). The item sets of two sources are
            # disjoint, so a single row summing them is a number about nothing.
            per_source = entry.get("sources") or {"": entry}
            for source_id, block in sorted(per_source.items()):
                for run_id, counts in sorted((block.get("per_run") or {}).items()):
                    if not counts.get("rows"):
                        continue
                    row = ["`%s`" % (source_id or "(unstamped)"), "`%s`" % run_id,
                           counts.get("rows"), counts.get("items"),
                           counts.get("collapsed_items")]
                    key = (source_id or "", run_id)
                    previous = seen_runs.get(key)
                    if previous is None:
                        seen_runs[key] = (row, os.path.basename(tally["file"]))
                        per_run_rows.append(row)
                    elif previous[0][2:] != row[2:]:
                        stale.append(
                            "`%s` / `%s`: %s says %s row(s), %s says %s -- one of them is "
                            "stale; re-run `cf tally`."
                            % (source_id or "(unstamped)", run_id, previous[1],
                               previous[0][2], os.path.basename(tally["file"]), row[2])
                        )
                raw = block.get("raw") or {}
                totals.append(
                    [
                        os.path.basename(tally["file"]),
                        "`%s`" % (source_id or "(unstamped)"),
                        block.get("rows"),
                        raw.get("items"),
                        raw.get("substantive"),
                        raw.get("high"),
                        ", ".join("%s %s" % (v, k)
                                  for k, v in sorted((block.get("by_kind") or {}).items())),
                        _classes(block),
                    ]
                )
        if per_run_rows:
            blocks += [_table(["source", "run", "rows", "raw items", "source items"],
                              per_run_rows), ""]
        for note in stale:
            blocks += ["> **Stale tally.** " + note, ""]
        if totals:
            blocks += [
                _table(["tally", "source", "rows", "items", "substantive", "high",
                        "by kind", "classes"], totals),
                "",
            ]
        if not per_run_rows and not totals:
            blocks += [todo("tally for %s" % name, "cf harvest --tally"), ""]

        meta = f["routed_meta"] or {}
        if meta:
            counts = meta.get("counts") or {}
            blocks += [
                "Routed: **%s** — %s row(s) over %s item(s)%s; %s challenged, %s unchallenged, "
                "%s withdrawn. Rule: %s."
                % (
                    meta.get("status", ""),
                    counts.get("rows", "?"),
                    counts.get("items", "?"),
                    (" (" + ", ".join("%s: %s" % (sid, n) for sid, n
                                      in sorted((counts.get("by_source") or {}).items())) + ")")
                    if counts.get("by_source") else "",
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


def render_paths_list(paths: Any, facts: list[dict], source_ids: Iterable[str] = ()) -> str:
    """Section 6."""
    lines = [
        "- Workspace directory (private git repo, no remote): `%s`" % paths.root,
        "- Corpus, append-only per run: `%s`" % paths.corpus(),
        "- Ledger: `%s`" % paths.ledger,
        "- Routed files and owner-message drafts: `%s`" % paths.routed,
        "- Reports: `%s`" % paths.reports,
    ]
    for source_id in source_ids:
        lines.append("- Source `%s`: text and page map `%s`; pairing `%s`"
                     % (source_id, paths.source_dir(source_id), paths.pairs(source_id)))
    lines.append(
        "- Source text, page images and crops are gitignored and never leave the "
        "workspace directory.")
    for f in facts:
        repo = (f["state"] or {}).get("repo")
        if repo:
            lines.append("- Companion `%s` (read-only to this pipeline): `%s`" % (f["name"], repo))
    return "\n".join(lines)


DEFAULT_TRAPS = [
    "Two editions of one work are two SOURCES: a page anchor is only meaningful against the "
    "source_id and source_sha the row carries, and the edition a companion's anchors follow is not "
    "always one it ships. A finding judged against another printing is not a finding.",
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
             "python3 scripts/cf.py route --workspace %s" % paths.root,
             "# ledger counts, and that every row answers a real finding",
             "python3 scripts/cf.py adjudicate --workspace %s --validate" % paths.root,
             "# the tally every finding number comes from, grouped by (source_id, repo)",
             "python3 scripts/cf.py tally --workspace %s" % paths.root]
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


def render_sources_table(cfg: Any, tallies: list[dict]) -> str:
    """The handoff's sources table: one row per source, with its companions."""
    rows = []
    for source_id in cfg.source_ids():
        src = cfg.source(source_id)
        bound = [n for n in cfg.companion_names() if source_id in (cfg.sources_of(n) or [])]
        anchors = [n for n in bound if cfg.anchor_source(n) == source_id]
        rows.append([
            "`%s`" % source_id,
            (src.sha256 or todo("source_sha", "cf onboard"))[:12],
            src.edition or src.title or "",
            src.offset,
            ", ".join("`%s`" % n for n in bound) or "(none)",
            ", ".join("`%s`" % n for n in anchors) or "—",
            "yes" if src.confirmed else "**no**",
        ])
    if not rows:
        return todo("sources", "cf onboard <repo>")
    return _table(["source", "sha256", "edition", "offset", "companions", "anchored by",
                   "confirmed"], rows)


def build_context(
    workspace: str,
    *,
    tally_files: Iterable[str] = (),
    gh_files: Iterable[str] = (),
    date: str = "",
    tldr: str = "",
    wrong_section: str = "",
    traps: Iterable[str] = (),
) -> dict:
    """Compute every substitution for templates/handoff.md."""
    cfg = compat.load_workspace(workspace)
    paths = cfg.paths
    names = cfg.companion_names()
    facts = [companion_facts(paths, n, cfg.companion(n)) for n in names]
    tallies = load_tallies(paths, tally_files)
    runs = load_runs(paths)
    receipts = load_receipts(paths)
    gh = load_gh(gh_files)
    source_ids = cfg.source_ids()

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
        "%d companion(s) audited against %d source(s) (%s); %d open ledger row(s) across "
        "%d routed file(s); %s"
        % (
            len(facts),
            len(source_ids),
            ", ".join("`%s`" % s for s in source_ids) or todo("sources", "cf onboard"),
            open_total,
            sum(1 for f in facts if f["routed_meta"]),
            "no upstream report may be produced yet." if unchallenged
            else "every routed file is challenged.",
        )
    )
    return {
        "workspace": cfg.title or os.path.basename(paths.root),
        "date": date or _dt.date.today().isoformat(),
        "sources_table": render_sources_table(cfg, tallies),
        "tldr": tldr or default_tldr,
        "state_table": render_state_table(facts),
        "status_note": status_note,
        "runs_table": render_runs_table(runs, tallies, receipts),
        "yield_note": (
            "Every cell above is `run.yaml`, a corpus `RECEIPT.json` or a tally file; no number "
            "here was typed by hand. `--` means an imported run records no such figure."
            if tallies else todo("yield table", "cf harvest --tally")
        ),
        "findings_tables": render_findings_tables(facts, tallies),
        "owner_actions": render_owner_actions(facts, gh),
        "wrong_section": wrong_section or (
            "_%s — this section is written by the person who ran the pass. It is the transferable "
            "part of the report and no script can fill it._" % todo("what went wrong")
        ),
        "paths_list": render_paths_list(paths, facts, source_ids),
        "traps_list": render_traps(traps),
        "verification_commands": render_verification(paths, facts, tallies, gh),
        "provenance": "_Generated by `cf report` from %d tally file(s), %d run.yaml file(s), %d "
        "corpus receipt(s) and %d saved gh output file(s). Figures with no artifact behind them "
        "are marked TODO._"
        % (len(tallies), len(runs), len(receipts), len(gh)),
        "_facts": facts,
        "_tallies": tallies,
        "_paths": paths,
        "_workspace": cfg,
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
    workspace: str,
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
        workspace,
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
    ap = argparse.ArgumentParser(description="Render the workspace handoff from computed artifacts.")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--tally", action="append", default=[])
    ap.add_argument("--gh-json", action="append", default=[])
    ap.add_argument("--out", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--tldr", default="")
    args = ap.parse_args(argv)
    report = make_handoff(
        args.workspace,
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
