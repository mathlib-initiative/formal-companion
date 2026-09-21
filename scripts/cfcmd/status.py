"""cf status -- what this workspace knows, read from state.json only.

New code.  `cf status` prints counts that were
written by the command that produced them; it never re-derives a number by
reading findings, because every counting error in the audit so far ran toward
"cleaner".  A number that is missing here means the stage has not run, not
that it found nothing.

It also prints the SOURCES table and the "unconfirmed sources" block: discovery
writes what it learned about a source nobody had catalogued with
``confirmed: false``, and the only place that would otherwise be visible is a
YAML file nobody reads.

The first line reports the two facts the workspace's copyright story rests on --
whether the repo holding it has a remote, and whether the cf pre-push reminder is
in place.  `cf init` refuses a remote at creation time and that refusal is the
control, but a remote added afterwards passes through nothing: printing it on
every run is what catches it.  For a workspace created with `--allow-nested` the
repo in question is the ENCLOSING one, which is where `cf init` put the reminder
and the ignore rules.

    cf status
    cf status --companion ours
    cf status --source tao-measure-gsm126-published
    cf status --json
"""
from __future__ import annotations

import json

from cflib import log, state as state_mod
from cflib.errors import CfError, StateError
from cflib.hashing import short
from cflib.jsonio import read_json
from cflib.log import format_table
from cflib.workspace import from_args

COLUMNS = ["companion", "commit", "adapter", "sources", "anchor", "rows", "items",
           "single_source", "runs", "findings", "confirmed", "refuted", "routed",
           "stale", "updated"]

SOURCE_COLUMNS = ["source", "sha", "offset", "conf", "items", "errata", "companions",
                  "role", "confirmed"]


def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="print the sources table and every companion's state")
    p.add_argument("--companion", help="show one companion in full")
    p.add_argument("--source", help="show one source's row only")
    p.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    p.add_argument("--runs", action="store_true", help="also list runs and corpus sizes")


def run(args) -> int:
    workspace = from_args(args)
    names = [args.companion] if args.companion else workspace.companion_names()

    states: dict[str, dict] = {}
    missing: list[str] = []
    for name in names:
        try:
            states[name] = state_mod.read_state(workspace, name)
        except StateError:
            missing.append(name)

    if args.as_json:
        print(json.dumps({
            "workspace": _workspace_facts(workspace),
            "sources": _source_facts(workspace),
            "companions": states,
            "not_onboarded": missing,
            "runs": _run_facts(workspace),
        }, indent=2, ensure_ascii=False))
        return 0

    facts = _workspace_facts(workspace)
    log.info(f"workspace : {workspace.root}   ({facts['name']})")
    _print_git_guard(facts["git"])
    sources = _source_facts(workspace)
    if args.source:
        sources = [s for s in sources if s["id"] == args.source]
        if not sources:
            raise CfError(f"unknown source {args.source!r}",
                          hint=f"known: {', '.join(workspace.source_ids()) or '(none)'}")
    if not sources:
        #: An unreadable registry is not an empty workspace.  `cf status` catches the
        #: CfError further down so that a broken file cannot kill the one command you
        #: run to find out what is broken -- but falling through to "none yet" reported
        #: a workspace whose overlay names sources as having none, which is worse than
        #: failing.  Say which it is.
        unreadable = _registry_error(workspace)
        if unreadable:
            log.warn(f"sources   : UNKNOWN -- {workspace.paths.registry_yaml} could not be read")
            log.info(f"            {unreadable}")
            log.info("")
            log.info("next:  fix that file (it is written by cf, so this is a bug worth "
                     "reporting), then re-run `cf status`")
        else:
            log.info("sources   : none yet")
            log.info("")
            log.info("next:  cf onboard <path to a companion repo>")
    else:
        log.info("")
        log.info(format_table(SOURCE_COLUMNS, [
            [s["id"], short(s["sha256"], 8) if s["sha256"] else "-", s["offset"],
             f"{s['offset_confidence']:.2f}", s["items"] if s["items"] is not None else "-",
             _yn(s["errata"]), ",".join(s["companions"]) or "-", s["role"],
             "yes" if s["confirmed"] else "NO"]
            for s in sources]))
        for s in sources:
            if not s["prepared"]:
                log.warn(f"{s['id']}: not prepared (no text.txt) -- the quote gate cannot run. "
                         f"cf sources refresh {s['id']}")
            elif not s["sha256"]:
                log.warn(f"{s['id']}: no sha256 -- the source gate cannot run")
            if s["text_drift"]:
                log.warn(f"{s['id']}: the prepared text no longer hashes to meta.json's "
                         "text_sha256 (poppler changed?) -- "
                         f"cf sources refresh {s['id']} --force")
        shared = workspace.shared_sources()
        log.info("")
        for sid, companions in shared.items():
            log.info(f"differential possible on {sid}: {', '.join(companions)}")
        if not shared and len(workspace.companions) > 1:
            log.info("no two companions share a source, so no differential is possible")

    if args.companion and args.companion not in states:
        raise CfError(f"companion {args.companion!r} has no state.json",
                      hint=f"run `cf onboard <repo> --name {args.companion}`"
                           + (f"; known: {', '.join(workspace.companion_names())}"
                              if workspace.companion_names() else ""))
    if not states:
        log.info("")
        log.info("no companions onboarded yet:")
        log.info("  cf onboard <path to a companion repo>")
        _print_unconfirmed(workspace)
        return 0

    rows = []
    for name in names:
        st = states.get(name)
        if st is None:
            rows.append([name] + ["-"] * (len(COLUMNS) - 2) + ["not onboarded"])
            continue
        row = state_mod.summary_row(st)
        # summary_row counts the sources the last EXTRACT touched. The workspace
        # is what the companion is BOUND to, and the two differ the moment a
        # source is added and nothing has re-extracted yet -- printing only the
        # first contradicts the source table directly above, which already lists
        # this companion against the new source.
        bound = len(workspace.sources_of(name))
        done = row.get("sources") or 0
        row["sources"] = str(bound) if bound == done else f"{bound} ({done} extracted)"
        rows.append([row.get(c) for c in COLUMNS])
    log.info("")
    log.info(format_table(COLUMNS, rows))

    for name in names:
        bound = set(workspace.sources_of(name))
        done = set((states.get(name) or {}).get("sources") or {})
        missing = sorted(bound - done)
        if missing:
            log.warn(f"{name}: bound to {', '.join(missing)} but no rows extracted against "
                     f"{'it' if len(missing) == 1 else 'them'} -- run "
                     f"`cf extract --companion {name}`")

    for name in names:
        st = states.get(name)
        if st is None:
            continue
        for sid, body in (st.get("sources") or {}).items():
            want = (workspace.sources.get(sid).sha256 if sid in workspace.sources else None)
            if body.get("sha") and want and body["sha"] != want:
                log.warn(f"{name}: state.json sources.{sid}.sha {short(body['sha'])} != workspace "
                         f"{short(want)} -- re-run `cf extract --companion {name}`")
        if st.get("stale_since_commit"):
            log.warn(f"{name}: statement table is stale since {short(st['stale_since_commit'], 7)}")
        routed = st.get("routed") or {}
        if routed.get("status") == "UNCHALLENGED":
            log.warn(f"{name}: routed findings are UNCHALLENGED -- do not report them upstream "
                     "(run `cf adjudicate` first)")

    _print_unconfirmed(workspace)
    if args.companion:
        _print_detail(states.get(args.companion) or {}, args.companion)
    if args.runs or args.verbose:
        _print_runs(workspace)
    return 0


def _print_unconfirmed(workspace) -> None:
    """Discovery writes `confirmed: false`; this is where a human sees it."""
    _print_registry_problems(workspace)
    unconfirmed = workspace.unconfirmed_sources()
    if not unconfirmed:
        return
    log.info("")
    log.warn("unconfirmed sources (discovered, nobody has checked them)")
    for ref in unconfirmed:
        origin = ", ".join((ref.discovered or {}).get("from") or []) or ref.registry
        log.info(f"  {ref.id}   sha {short(ref.sha256, 8) + '…' if ref.sha256 else '(none)'}"
                 f"   from {origin}")
        log.info(f"      confirm with:  cf sources show {ref.id}")
        log.info(f"                     cf sources set {ref.id} confirmed=true")
        log.info("      or promote it into the skill's registry/sources.yaml")


def _registry_error(workspace) -> str:
    """The first line of the error `Registry.load` raises, or "" when it loads."""
    from cflib.registry import Registry
    try:
        Registry.load(workspace)
    except Exception as exc:  # noqa: BLE001 - any breakage here is a report, not a crash
        return str(exc).strip().splitlines()[0]
    return ""


def _print_registry_problems(workspace) -> None:
    """Problems with the registry this workspace reads, chiefly a shadowed entry.

    A workspace that discovered a source before the skill catalogued it keeps an
    overlay row for it; once the skill ships a CONFIRMED entry for the same id,
    that row adds nothing and hides the curated facts behind a stale copy.  The
    merge keeps the skill's `confirmed: true`, and this is where the row itself
    gets reported so it can be deleted.
    """
    from cflib.registry import Registry
    try:
        problems = Registry.load(workspace).validate()
    except Exception as exc:  # noqa: BLE001 - status must never die on a bad registry
        log.warn(f"registry could not be read: {exc}")
        return
    if not problems:
        return
    log.info("")
    log.warn(f"registry ({len(problems)} problem(s))")
    for problem in problems[:8]:
        log.info(f"  {problem}")


# ---------------------------------------------------------------------------


def _yn(value) -> str:
    return "yes" if value else "no"


def _workspace_facts(workspace) -> dict:
    return {
        "root": str(workspace.root),
        "name": workspace.name,
        "companions": workspace.companion_names(),
        "sources": workspace.source_ids(),
        "shared_sources": workspace.shared_sources(),
        "hosts_allowlist": workspace.host_allowlist,
        "git": _git_guard_facts(workspace),
    }


def _git_guard_facts(workspace) -> dict:
    """Which repo guards this workspace, does it have a remote, is the hook there?

    Read fresh from the repo every time, because both facts can change after
    `cf init` -- `git remote add` and a hook somebody moved aside go through no
    cf command at all.

    The guarded repo is not always the workspace itself: `cf init --allow-nested`
    deliberately leaves the workspace without a ``.git`` of its own and installs
    the reminder and the ignore rules in the ENCLOSING repo.  Keying on
    ``root/.git`` alone called that supported layout "not a git repo" and, worse,
    never looked at the remotes of the repo that actually holds the corpus -- the
    one case (a remote added after init) this line exists to catch.
    """
    from cfcmd.init import git_toplevel, hook_state, no_remote_guard_set, remotes

    root = workspace.root
    guard = root if (root / ".git").exists() else git_toplevel(root)
    policy = workspace.policy or {}
    return {
        "root": str(root),
        "repo": guard is not None,
        "guarding": str(guard) if guard else "",
        "nested": bool(guard and guard != root),
        "remotes": remotes(guard) if guard else [],
        "pre_push_hook": hook_state(guard) if guard else "no-repo",
        # `cf init --no-remote-guard`: the one guard a `git push --no-verify` does
        # not walk past.  Recorded on every run, because it is local config and
        # `git config --unset` takes it away without passing through any cf command.
        "no_remote_guard": bool(guard and no_remote_guard_set(guard)),
        "allow_remote": bool(policy.get("allow_remote", False)),
        # `cf init --no-git` records policy.git: false. A workspace that was MEANT
        # to have a repo either has one or is nested inside one, so the warning
        # below is only ever about a workspace that lost the repo it should have.
        "policy_git": bool(policy.get("git", True)),
    }


def _print_git_guard(git: dict) -> None:
    """One line, every run: the workspace itself stays local, or it does not."""
    if not git["repo"]:
        if not git["policy_git"]:
            log.info("git       : no git repo (created with --no-git): no history for the "
                     "corpus and no pre-push reminder")
            return
        log.warn("this workspace is not a git repo: no history for the corpus and no pre-push "
                 "reminder -- `cf init <workspace>` sets both up")
        return
    hook = {"cf": "cf pre-push reminder installed",
            "foreign": "pre-push hook is NOT the cf one",
            "absent": "NO cf pre-push reminder",
            "no-hooks-dir": "no .git/hooks directory, so no reminder"}[git["pre_push_hook"]]
    # Stated only when it is set: a standing suggestion to pass a flag is the nag
    # `--no-git` was fixed for, and `cf init` already names the flag once.
    if git.get("no_remote_guard"):
        hook += "; bare-push guard set"
    where = (f"guarding {git['guarding']} (this workspace is nested inside it) -- "
             if git["nested"] else "")
    whose = "the repo holding this workspace" if git["nested"] else "this workspace repo"
    if git["remotes"]:
        log.info(f"git       : {where}remote {', '.join(git['remotes'])}   ({hook})")
        log.warn(f"{whose} HAS a remote: corpus/ quotes the source and the ledgers "
                 "hold unchallenged findings, so the workspace itself stays local -- run "
                 f"`git -C {git.get('guarding') or '<workspace>'} remote remove "
                 f"{git['remotes'][0]}`, "
                 "and share an export instead (`cf share export --out <bundle>` for the "
                 "campaign, `cf route --redact-quotes` for one companion's findings upstream)")
    else:
        log.info(f"git       : {where}no remote (the workspace stays local)   ({hook})")
    if git["pre_push_hook"] != "cf":
        restore = "cf init <workspace>" + (" --allow-nested" if git["nested"] else "")
        log.warn(f"{hook}: the reminder that a push carries book quotes is missing. It is only a "
                 "reminder -- `git push --no-verify` and a fresh clone both defeat it, and having "
                 f"no remote is the real control -- but re-run `{restore}` to restore it")


def _source_facts(workspace) -> list[dict]:
    """One row per source, read from workspace.yaml and the prepared meta.json."""
    paths = workspace.paths
    out = []
    for sid, ref in workspace.sources.items():
        items_json = read_json(paths.source_items(sid), default={}) or {}
        meta = read_json(paths.source_meta(sid), default={}) or {}
        text = paths.source_text(sid)
        drift = False
        if text.exists() and meta.get("text_sha256"):
            from cflib.hashing import sha256_text
            drift = sha256_text(text.read_text(encoding="utf-8", errors="ignore")) != meta["text_sha256"]
        out.append({
            "id": sid,
            "sha256": ref.sha256,
            "title": ref.title,
            "edition": ref.edition,
            "offset": ref.offset,
            "offset_confidence": ref.offset_confidence,
            "offset_method": ref.offset_method,
            "items": items_json.get("count"),
            "kinds": list(ref.kinds),
            "chapters": list(ref.chapters),
            "errata": paths.source_errata_md(sid).exists(),
            "errata_url": ref.errata_url,
            "prepared": text.exists(),
            "text_drift": drift,
            "companions": workspace.companions_of(sid),
            "role": ref.role,
            "confirmed": ref.confirmed,
            "registry": ref.registry,
            "pdf": str(ref.pdf) if ref.pdf else None,
        })
    return out


def _run_facts(workspace) -> list[dict]:
    paths = workspace.paths
    out = []
    for run_id in paths.list_runs():
        corpus = paths.corpus_run_dir(run_id)
        receipt = paths.receipt_json(run_id)
        out.append({
            "run_id": run_id,
            "corpus_files": len(list(corpus.glob("*.json"))) - (1 if receipt.exists() else 0)
                            if corpus.is_dir() else 0,
            "receipt": receipt.exists(),
        })
    for corpus in sorted(paths.corpus_dir.glob("*")) if paths.corpus_dir.is_dir() else []:
        if corpus.is_dir() and corpus.name not in {r["run_id"] for r in out}:
            out.append({"run_id": corpus.name,
                        "corpus_files": len(list(corpus.glob("*.json"))),
                        "receipt": (corpus / "RECEIPT.json").exists(),
                        "planned": False})
    return out


def _print_runs(workspace) -> None:
    runs = _run_facts(workspace)
    if not runs:
        return
    log.info("")
    log.info(format_table(["run", "corpus files", "receipt"],
                          [[r["run_id"], r["corpus_files"], _yn(r["receipt"])] for r in runs]))


def _print_detail(state: dict, name: str) -> None:
    if not state:
        raise CfError(f"no state.json for companion {name!r}",
                      hint=f"run `cf onboard <repo> --name {name}`")
    log.info("")
    log.info(f"--- {name} ---")
    for section in state_mod.SECTIONS:
        body = state.get(section)
        if not body:
            continue
        log.info(f"{section}:")
        for key, value in body.items():
            if isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)
            log.info(f"    {key}: {value}")
    history = state.get("history") or []
    if history:
        log.info("history:")
        for entry in history[-5:]:
            extra = {k: v for k, v in entry.items() if k not in {"at", "event"}}
            log.info(f"    {entry['at']}  {entry['event']}  {extra if extra else ''}".rstrip())
