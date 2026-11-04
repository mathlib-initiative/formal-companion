"""`cf informalize status`: where one informalize id stands, read-only.

New code, for docs/proposals/informalize.md sections 3 and 13: the settings, the output
repository (its HEAD and whether it is clean, the runtime against the templates, the
license), each stage's receipt, the item table, each gate's approval (absent, current, or
stale and why), and the command that comes next.  It prints only what it read.
"""
from __future__ import annotations

from cflib.errors import CfError
from cflib.vocab import APPROVAL_GATES

from informalize import approve, config, project, runtime, stages
from informalize.outrepo import OutputRepo

STAGES = ("I0", "I1", "I2", "I3", "I4", "I6", "I7")


def report(workspace, informalize_id: str) -> list[tuple[str, str]]:
    """``[(label, value)]``, in the order a person reads them."""
    paths = workspace.paths
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    library = cfg["library"]
    rows = [("title", cfg["text"]["title"]),
            ("library", f"{library['url']} @ {library['commit'][:12]}"),
            ("theorem", f"{library['theorem']} ({library['module']})"),
            ("license", f"text {cfg['text']['license']}; library "
                        f"{library.get('license') or 'not yet known (I1)'}")]
    repo = OutputRepo(paths, informalize_id)
    if not repo.exists():
        rows.append(("repository", f"{repo.root}: not created; `cf informalize init` creates it"))
        return rows
    head = repo.head()
    changes = repo.changes()
    rows.append(("repository", f"{repo.root} at {head[:12] if head else 'no commit'}, "
                 + (f"{len(changes)} uncommitted change(s)" if changes else "clean")))
    try:
        repo.require_stamp()
        problems = runtime.problems(repo, cfg)
    except CfError as exc:
        problems = [exc.message]
    rows.append(("runtime", "current" if not problems else
                 f"{len(problems)} problem(s), the first: {problems[0]}; "
                 f"`cf informalize sync {informalize_id}`"))
    rows.append(("LICENSE", f"the text of {cfg['text']['license']}"
                 if project.license_matches(cfg, repo.read_bytes("LICENSE"))
                 else f"not the text of {cfg['text']['license']}"))
    done = []
    for stage in STAGES:
        receipt = stages.read_receipt(paths, informalize_id, stage)
        done.append(f"{stage} {receipt['written_at']}" if receipt else f"{stage} -")
    rows.append(("stages", ", ".join(done)))
    rows.append(("items", _items(workspace, informalize_id)))
    for gate in APPROVAL_GATES:
        state, stale = approve.gate_state(workspace, informalize_id, gate)
        approval = stages.read_approval(paths, informalize_id, gate)
        detail = f" by {approval['by']} at {approval['at']}" if approval else ""
        rows.append((f"approve {gate}", state + detail + (f" ({stale[0]})" if stale else "")))
    rows.append(("next", next_step(workspace, informalize_id)))
    return rows


def _items(workspace, informalize_id: str) -> str:
    table = approve.Table(workspace, informalize_id)
    try:
        doc = table.items()
    except CfError:
        return "no items.json yet (the items phase writes it)"
    items = [it for it in doc.get("items") or [] if isinstance(it, dict)]
    in_v1 = sum(1 for it in items if it.get("in_v1") is True)
    text = (f"{len(items)} items ({in_v1} in version 1) in {len(doc.get('sections') or [])} "
            f"sections, {len(doc.get('deferred') or [])} deferred")
    try:
        problems = table.check(doc, numbered=table.numbered())
    except CfError as exc:
        return f"{text}; not checked: {exc.message}"
    frozen = table.frozen_since()
    return (f"{text}; check-items: "
            + ("ok" if not problems else f"{len(problems)} problem(s)")
            + ("; ids frozen" if frozen else ""))


def next_step(workspace, informalize_id: str) -> str:
    """The next command, from what is there.  Several are not implemented yet:
    build-command and scope (component 4) and `cf plan-informalize` (5a)."""
    paths = workspace.paths
    if stages.read_receipt(paths, informalize_id, "I0") is None:
        return f"cf informalize sync {informalize_id}"
    if not paths.informalize_library_src(informalize_id).is_dir():
        return f"cf informalize build-command {informalize_id} (stage I1)"
    table = approve.Table(workspace, informalize_id)
    if not (table.repo.root / approve.DECLARATIONS).is_file():
        return f"cf informalize scope {informalize_id} (stage I2)"
    if not (table.repo.root / approve.ITEMS).is_file():
        return f"cf plan-informalize {informalize_id} (the items phase, stage I3)"
    for gate in ("items", "references"):
        state, _ = approve.gate_state(workspace, informalize_id, gate)
        if state != "current":
            if gate == "references" and not paths.informalize_reference_checklist(
                    informalize_id).is_file():
                return f"cf informalize reference-checklist {informalize_id}"
            return f"cf informalize approve {informalize_id} {gate} --by <name>"
    return f"cf plan-informalize {informalize_id} (the conventions phase, stage I4)"
