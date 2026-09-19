"""cf init -- create a workspace: N companions, M sources.

New code (docs/design/multi-source-model.md S7.1).  The workspace directory is
the ~/Desktop/tao-differential role: a local git repo with NO remote holding the
source text, the statement tables, the findings corpus and the ledgers.  The
skill repo holds code, templates and docs only; workspace data never enters it.

**It takes no source flags.**  There is nothing to say yet: a source arrives
with `cf onboard <repo>`, which discovers it from the repository, fingerprints
the PDF and infers the offset, the kinds and the errata page (R2).

    cf init ~/Desktop/tao-workspace
    cf onboard ~/Desktop/tao-measure-lean
"""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from cflib import log
from cflib.errors import CfError
from cflib.jsonio import atomic_write
from cflib.paths import WorkspacePaths, skill_root
from cflib.registry import OVERLAY_FILE, REGISTRY_VERSION
from cflib.templates import render_template
from cflib.workspace import WORKSPACE_FILE, Workspace

GITIGNORE = """\
# Written by `cf init`. The workspace directory is a LOCAL git repo with no remote.
#
# Copyrighted source material never enters git:
sources/
**/pages/
**/images/
**/crops/
*.pdf

# Agent transcripts, tend logs and its sqlite copy quote the book verbatim:
runs/*/tend-root/
runs/*/logs/
**/sessions/

# Large derived artifacts:
detect/*/deps.json

# corpus/ is deliberately NOT ignored: the findings corpus is the calibration
# baseline and must be durable. It quotes 1-3 book sentences per finding, so the
# repo stays local (see the pre-push hook) and exports run with --redact-quotes.
#
# registry.yaml is NOT ignored either: it is metadata about editions (shas, URLs,
# page maps), never a line of the text itself.

__pycache__/
*.pyc
.DS_Store
"""

PRE_PUSH_HOOK = """\
#!/bin/sh
# Installed by `cf init`. This workspace repo holds copyrighted book quotes,
# private Lean statements and UNCHALLENGED findings; it is not meant to be
# pushed anywhere. Set CF_ALLOW_PUSH=1 for a deliberate, reviewed exception.
if [ "$CF_ALLOW_PUSH" != "1" ]; then
    echo "pre-push refused: workspace repositories have no remote by default." >&2
    echo "  book quotes + unchallenged findings live here; export instead" >&2
    echo "  (cf report --redact-quotes), or set CF_ALLOW_PUSH=1 to override." >&2
    exit 1
fi
exit 0
"""


REGISTRY_OVERLAY = """\
# registry.yaml -- this WORKSPACE's overlay on the skill's source registry.
#
# `cf onboard` and `cf sources add` append what they learn about a source nobody
# had catalogued, always with `confirmed: false`, and `cf status` lists those
# until a human checks them. Promote an entry by copying it into the skill's
# registry/sources.yaml (confirmed: true) and deleting it here.
#
# METADATA ONLY -- titles, editions, shas, URLs, page maps, licence notes. Never
# a line of the source text: that is why this file can be committed.
version: 1
sources: {}
"""


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "init", help="create a workspace (N companions, M sources)",
        description=__doc__.split("\n\n")[1] if __doc__ else None)
    p.add_argument("directory", help="workspace directory to create (outside every repo)")
    p.add_argument("--name", help="workspace name (default: the directory name)")
    p.add_argument("--no-git", action="store_true", help="do not run git init")
    p.add_argument("--allow-nested", action="store_true",
                   help="create the workspace inside an existing git repo anyway; the pre-push "
                        "hook and the ignore rules are then installed in THAT repo. Refused "
                        "outright when the enclosing repo has a remote")
    p.add_argument("--force", action="store_true", help="overwrite an existing workspace.yaml")


def run(args) -> int:
    root = Path(args.directory).expanduser().resolve()
    _refuse_unsafe_location(root)
    # BEFORE anything is written: the refusal is worthless once the workspace's
    # .gitignore and workspace.yaml are already untracked content of the outer repo.
    if not args.no_git:
        _refuse_nested_git(root, allow_nested=args.allow_nested)
    root.mkdir(parents=True, exist_ok=True)

    paths = WorkspacePaths(root=root)
    made = paths.ensure_skeleton()
    for d in made:
        log.detail(f"created {d}")

    _write_if_absent(paths.gitignore, GITIGNORE, args.force)
    _write_if_absent(paths.registry_yaml, REGISTRY_OVERLAY, args.force)

    workspace_yaml = root / WORKSPACE_FILE
    if workspace_yaml.exists() and not args.force:
        log.info(f"{WORKSPACE_FILE} already exists; left untouched (use --force to rewrite)")
    else:
        text = render_template("workspace.yaml.example", {
            "workspace_name": args.name or root.name,
            "created": _quote(date.today().isoformat()),
        })
        atomic_write(workspace_yaml, text)
        log.ok(f"wrote {workspace_yaml}")

    workspace = Workspace.load(root)  # validates what we just wrote
    if not args.no_git:
        _init_git(root, allow_nested=args.allow_nested,
                  allow_remote=bool(workspace.policy.get("allow_remote", False)))
    log.info("")
    log.info(f"workspace : {workspace.root}")
    log.info(f"sources   : {', '.join(workspace.source_ids()) or 'none yet'}")
    log.info(f"companions: {', '.join(workspace.companion_names()) or 'none yet'}")
    # The search roots are also the ALLOWLIST for an absolute path a companion
    # repo names in its own files, so a user who never opens workspace.yaml still
    # has to be told what this workspace will open.
    log.info("pdf search roots (workspace.yaml library_paths + derived):")
    for root_path in workspace.library_paths:
        log.info(f"  {root_path}")
    log.info("")
    log.info("next:")
    log.info(f"  cf --workspace {root} onboard <path to a companion repo>")
    log.info("      (no flags: the sources, the page offset, the item kinds and the")
    log.info("       errata page are discovered from the repo itself)")
    return 0


def _quote(text: str) -> str:
    return '"' + text.replace('"', '\\"') + '"'


def _write_if_absent(path: Path, text: str, force: bool) -> None:
    if path.exists() and not force:
        log.detail(f"{path} exists; left untouched")
        return
    atomic_write(path, text)
    log.ok(f"wrote {path}")


def _refuse_unsafe_location(root: Path) -> None:
    """Workspace data must not land inside the skill repo or a companion repo."""
    skill = skill_root()
    if root == skill or skill in root.parents:
        raise CfError(f"refusing to create a workspace inside the skill repo ({skill})",
                      hint="the skill repo holds code, templates and docs only; "
                           "put the workspace somewhere like ~/Desktop/<name>-workspace")
    if (root / ".lake").exists() or (root / "lakefile.toml").exists() or (root / "lakefile.lean").exists():
        raise CfError(f"{root} looks like a Lean companion repo",
                      hint="the workspace directory lives outside every companion repo")


def _nearest_existing(path: Path) -> Path:
    """The closest ancestor of *path* that exists (git needs a real -C)."""
    cur = path
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    return cur


def _refuse_nested_git(root: Path, *, allow_nested: bool) -> None:
    """Refuse a workspace nested inside an existing repository.

    `cf init` refuses the skill repo and a Lean companion, but a directory
    *inside* some other repository only warned "not running git init" and exited
    0.  No pre-push hook was installed anywhere -- the workspace had no .git of
    its own and the enclosing repo was untouched -- so corpus/ (1-3 verbatim book
    sentences per finding), ledger/, companions/*/statements.jsonl and
    runs/*/tasks/ became ordinary untracked content of a repo that may have a
    public remote.
    """
    if (root / ".git").exists():
        return
    top = _git_toplevel(_nearest_existing(root))
    if not top or top == root:
        return
    remotes = _remotes(top)
    if remotes:
        raise CfError(
            f"{root} is inside the git repo {top}, which has a remote ({remotes[0]})",
            hint="a workspace holds verbatim book quotes and UNCHALLENGED findings; put it "
                 "outside that repo. --allow-nested does NOT override this.")
    if not allow_nested:
        raise CfError(
            f"{root} is inside the git repo {top}",
            hint="workspace repos are standalone and have no remote; put the workspace outside "
                 "that repo, or pass --allow-nested to install the pre-push guard and the "
                 "workspace's ignore rules in the enclosing repo instead")


def _remotes(repo: Path) -> list[str]:
    res = subprocess.run(["git", "-C", str(repo), "remote"], capture_output=True, text=True)
    return res.stdout.split() if res.returncode == 0 else []


def _install_hook(repo: Path) -> None:
    """Install the pre-push guard in *repo* (never overwrites an existing hook)."""
    hook = repo / ".git" / "hooks" / "pre-push"
    if not hook.parent.is_dir():
        log.warn(f"{hook.parent} does not exist; no pre-push guard installed")
        return
    if hook.exists():
        log.detail(f"{hook} exists; left untouched")
        return
    atomic_write(hook, PRE_PUSH_HOOK)
    hook.chmod(0o755)
    log.ok(f"installed {hook}")


def _init_git(root: Path, allow_nested: bool = False, allow_remote: bool = False) -> None:
    """git init with no remote; install the pre-push guard.

    The nested case -- a workspace created INSIDE an existing repository -- used
    to warn and return, leaving no hook anywhere and no .git of its own.  The
    workspace's deliberately-not-ignored content (corpus/, ledger/,
    companions/*/statements.jsonl, runs/*/tasks/) then became ordinary untracked
    content of the outer repo, which may have a public remote, and the "workspace
    repos have no remote" invariant the whole copyright story rests on was
    silently never established.
    """
    git_dir = root / ".git"
    if not git_dir.exists():
        top = _git_toplevel(root)
        if top and top != root:
            # _refuse_nested_git() already refused the cases that must not happen.
            log.warn(f"{root} is inside {top}: guarding THAT repo instead of running git init")
            _install_hook(top)
            _append_ignore(top, root)
            return
        res = subprocess.run(["git", "init", "-q", str(root)], capture_output=True, text=True)
        if res.returncode != 0:
            log.warn(f"git init failed: {res.stderr.strip()}")
            return
        log.ok(f"git init {root} (no remote)")
    else:
        log.detail("git repo already present")
    remotes = _remotes(root)
    if remotes and not allow_remote:
        # workspace.yaml policy.allow_remote (false by default) is the rule; the
        # hook is the enforcement. Both, or the policy block is decoration.
        raise CfError(
            f"this workspace repo has a remote ({remotes[0]}) and workspace.yaml sets "
            "policy.allow_remote: false",
            hint="book quotes and UNCHALLENGED findings live here; run "
                 f"`git -C {root} remote remove {remotes[0]}`, or set policy.allow_remote: true "
                 "in workspace.yaml and take responsibility for every push")
    if remotes:
        log.warn(f"this workspace repo has a remote ({remotes[0]}) and policy.allow_remote is "
                 "true: book quotes and unchallenged findings must not be pushed")
    _install_hook(root)


def _append_ignore(outer: Path, workspace: Path) -> None:
    """Ignore the nested workspace from the enclosing repo's .gitignore."""
    rel = workspace.relative_to(outer).as_posix().rstrip("/")
    marker = f"/{rel}/"
    path = outer / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in existing.splitlines():
        log.detail(f"{path} already ignores {marker}")
        return
    block = (("" if existing.endswith("\n") or not existing else "\n")
             + f"\n# Added by `cf init --allow-nested`: workspace data (verbatim book quotes,\n"
               f"# UNCHALLENGED findings) must never be committed or pushed from this repo.\n"
               f"{marker}\n")
    path.write_text(existing + block, encoding="utf-8")
    log.ok(f"ignored {marker} in {path}")


def _git_toplevel(path: Path) -> Path | None:
    res = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return Path(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else None
