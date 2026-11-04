"""cf init -- create a workspace: N companions, M sources.

New code (docs/design/archive/multi-source-model.md S7.1).  The workspace directory
plays the role the v1 campaign's own directory did: a local git repo with NO
remote holding the source text, the statement tables, the findings corpus and
the ledgers.  The skill repo holds code, templates and docs only; workspace data
never enters it.

**It takes no source flags.**  There is nothing to say yet: a source arrives
with `cf onboard <repo>`, which discovers it from the repository, fingerprints
the PDF and infers the offset, the kinds and the errata page (R2).

    cf init <workspace>
    cf onboard <path to a companion repo>
"""
from __future__ import annotations

import shlex
import subprocess
from datetime import date
from pathlib import Path

from cflib import gitsafe, log
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
# ... including book text `cf quotes hydrate --out` recovered from this machine's
# own copy of a source, which is the directory that command suggests:
quotes/

# Agent transcripts, tend logs and its sqlite copy quote the book verbatim:
runs/*/tend-root/
runs/*/logs/
**/sessions/

# Large derived artifacts:
detect/*/deps.json

# The companion a pipeline generates is its own git repository (cf ingest, S0):
pipelines/*/repo/

# Informalize: the output repository is its own git repository (cf informalize init),
# and the library's copy and the canonical PDF are large and rebuilt from the host:
informalize/*/repo/
informalize/*/library/
informalize/*/pdf/

# Locate: a sourceset's page text sits under sourcesets/<id>/sources/, which the
# `sources/` line above already ignores. Library indexes and the two corpus databases are
# large derived artifacts, rebuilt from the probe output:
corpora/*/index/
corpora/*/corpus.sqlite
corpora/*/corpus.blind.sqlite
# The bench's planted copy of a universe database, and the labeler's args and
# copied results, which hold source excerpts:
benches/*/gold/planted/*.sqlite
benches/*/labeler/

# corpus/ is deliberately NOT ignored: the findings corpus is the calibration
# baseline and must be durable. It quotes 1-3 book sentences per finding, so the
# workspace itself stays local (no remote; the pre-push hook is only a reminder)
# and what is shared is an export: `cf share export` for a whole campaign,
# `cf route --redact-quotes` for one companion's findings upstream.
#
# registry.yaml is NOT ignored either: it is metadata about editions (shas, URLs,
# page maps), never a line of the text itself.

__pycache__/
*.pyc
.DS_Store
"""

#: how a cf-installed pre-push hook is recognised again, including one written by
#: an older version whose text has since been reworded.  `cf status` and
#: `_install_hook` both key on this rather than on byte equality.
HOOK_MARKER = "CF_ALLOW_PUSH"

PRE_PUSH_HOOK = """\
#!/bin/sh
# Installed by `cf init`. This workspace repo holds copyrighted book quotes,
# private Lean statements and UNCHALLENGED findings; it is not meant to be
# pushed anywhere. Set CF_ALLOW_PUSH=1 for a deliberate, reviewed exception.
#
# THIS HOOK IS A REMINDER, NOT THE CONTROL. `git push --no-verify` skips it, a
# fresh clone of this repo does not carry it, and nothing reinstalls it. The
# control is that the workspace repo has no remote -- `cf init` refuses one
# unless workspace.yaml sets policy.allow_remote, and `cf status` prints whether
# a remote has appeared since. `cf init --no-remote-guard` adds the stronger
# local-config guard, which a bare `git push --no-verify` does NOT get past.
if [ "$CF_ALLOW_PUSH" != "1" ]; then
    echo "pre-push refused: workspace repositories have no remote by default." >&2
    echo "  book quotes + unchallenged findings live here; share an export" >&2
    echo "  instead (cf share export --out <bundle>, which redacts the quotes," >&2
    echo "  or cf route --redact-quotes for one companion's findings upstream)," >&2
    echo "  or set CF_ALLOW_PUSH=1 to override." >&2
    echo "  NOTE: this hook is a reminder. git push --no-verify skips it." >&2
    exit 1
fi
exit 0
"""

#: the remote name `cf init --no-remote-guard` points ``remote.pushDefault`` at.
#: It deliberately does not exist: git resolves the default push remote before it
#: runs the pre-push hook, so a bare `git push --no-verify` fails on the remote
#: rather than on the hook it just skipped.
NO_REMOTE_GUARD_REMOTE = "cf-no-remote-guard"


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
    p.add_argument("--no-git", action="store_true",
                   help="do not run git init (recorded as policy.git: false, so `cf status` "
                        "reports the missing repo as a choice and not as a problem)")
    p.add_argument("--allow-nested", action="store_true",
                   help="create the workspace inside an existing git repo anyway; the pre-push "
                        "hook and the ignore rules are then installed in THAT repo. Refused "
                        "outright when the enclosing repo has a remote")
    p.add_argument("--no-remote-guard", action="store_true",
                   help="also set `remote.pushDefault` to a remote that does not exist and "
                        "`push.default nothing` in this repo's LOCAL config, so a bare `git "
                        "push` fails even with --no-verify. An explicit `git push <remote> "
                        "<ref>` still works: the control remains that the repo has no remote")
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
        if args.no_git:
            text = _record_no_git(text)
        atomic_write(workspace_yaml, text)
        log.ok(f"wrote {workspace_yaml}")

    workspace = Workspace.load(root)  # validates what we just wrote
    if not args.no_git:
        _init_git(root, allow_nested=args.allow_nested,
                  allow_remote=bool(workspace.policy.get("allow_remote", False)),
                  no_remote_guard=args.no_remote_guard)
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
    # `cd` first, then bare `cf <command>` -- the shell state README's quick start
    # teaches, and the one every other hint printer in the skill assumes. This
    # line used to carry `--workspace <root>` instead, which ran, but left the
    # operator OUTSIDE the workspace: `cf onboard`'s own next-steps are bare (as
    # are the other 546 hint sites), so the very next paste exited 2 with "no
    # workspace.yaml found". One outlier here was cheaper to fix than a flag
    # threaded into every hint printer. Shell-quoted, because `cd <a path with a
    # space>` is `cd: too many arguments` and the whole point of the line is
    # that it can be pasted.
    log.info(f"  cd {shlex.quote(str(root))}")
    log.info("  cf onboard <path to a companion repo>")
    log.info("      (no flags: the sources, the page offset, the item kinds and the")
    log.info("       errata page are discovered from the repo itself)")
    return 0


#: what `cf init --no-git` adds to workspace.yaml's policy block.  Without it the
#: only thing `cf status` could say about a repo-less workspace was a warning, so
#: a documented flag produced a standing instruction to undo it.
NO_GIT_POLICY = (
    "  # `cf init --no-git` created this workspace: it has no git repo of its own,\n"
    "  # so there is no history for the corpus and no pre-push reminder. `cf status`\n"
    "  # reports that as a choice rather than a warning.\n"
    "  git: false\n"
)


def _quote(text: str) -> str:
    return '"' + text.replace('"', '\\"') + '"'


def _record_no_git(text: str) -> str:
    """Record ``--no-git`` as ``policy.git: false`` in the rendered workspace.yaml."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("policy:"):
            lines.insert(i + 1, NO_GIT_POLICY)
            return "".join(lines)
    sep = "" if text.endswith("\n") else "\n"          # pragma: no cover - the template has one
    return text + sep + "\npolicy:\n" + NO_GIT_POLICY


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
                           "put the workspace somewhere like ~/<name>-workspace")
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
    top = git_toplevel(_nearest_existing(root))
    if not top or top == root:
        return
    remote_names = remotes(top)
    if remote_names:
        raise CfError(
            f"{root} is inside the git repo {top}, which has a remote ({remote_names[0]})",
            hint="a workspace holds verbatim book quotes and UNCHALLENGED findings; put it "
                 "outside that repo. --allow-nested does NOT override this.")
    if not allow_nested:
        raise CfError(
            f"{root} is inside the git repo {top}",
            hint="workspace repos are standalone and have no remote; put the workspace outside "
                 "that repo, or pass --allow-nested to install the pre-push guard and the "
                 "workspace's ignore rules in the enclosing repo instead")


def remotes(repo: Path) -> list[str]:
    """The workspace repo's remote names (empty when it has none, or is not a repo).

    Plain ``git`` on purpose and not ``cflib.gitsafe``: this is the operator's own
    workspace repository, not a companion tree whose ``.git/config`` is untrusted.
    """
    res = subprocess.run(["git", "-C", str(repo), "remote"], capture_output=True, text=True)
    return res.stdout.split() if res.returncode == 0 else []


def hook_state(repo: Path) -> str:
    """Is the cf pre-push reminder in *repo*?  ``cf``/``foreign``/``absent``/``no-hooks-dir``.

    A reminder is only worth anything if its absence is visible, and the two ways
    it goes missing silently are a hooks directory that is not there and somebody
    else's hook already occupying the path.  `cf status` prints this on every run.
    """
    hook = repo / ".git" / "hooks" / "pre-push"
    if not hook.parent.is_dir():
        return "no-hooks-dir"
    if not hook.exists():
        return "absent"
    body = hook.read_text(encoding="utf-8", errors="replace")
    return "cf" if HOOK_MARKER in body else "foreign"


def _config(repo: Path, key: str) -> str:
    """One value from *repo*'s LOCAL git config (``""`` when unset).

    Plain ``git`` for the same reason as :func:`remotes`: this is the operator's
    own workspace repository, not a companion tree whose config is untrusted.
    """
    res = subprocess.run(["git", "-C", str(repo), "config", "--local", "--get", key],
                         capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else ""


def check_ignore(repo: Path, path: Path) -> bool:
    """Would *repo* ignore *path*?  (`cf quotes hydrate --out` asks before writing.)

    NOT plain ``git``, unlike :func:`remotes` and :func:`_config`: those are only
    ever asked about the operator's own workspace repository, while the repo this
    one is asked about is derived from a path the operator NAMES (``--out``), and
    that can be any tree on the machine.  ``git check-ignore`` refreshes the index,
    an index refresh runs ``core.fsmonitor``, and ``core.fsmonitor`` is a command
    the repository's own ``.git/config`` chooses -- so the safety check was itself
    an execve for any repo that wanted one.  ``cflib.gitsafe`` pins that config and
    does not change the answer: measured identical verdicts on an ignored path, an
    unignored one, a committed .gitignore and an uncommitted one.  It still reads
    the user's own ``~/.gitconfig``, so ``core.excludesFile`` applies; only
    ``/etc/gitconfig`` is dropped (``GIT_CONFIG_NOSYSTEM``).

    ``git check-ignore`` is the only authority on the answer, because the rule may
    come from the workspace's .gitignore, an enclosing repo's, or the user's global
    excludes -- and because a workspace created before `cf init` learned to ignore
    ``quotes/`` has no such rule at all.  A git that hangs answers ``False``, which
    makes the caller refuse the write rather than raise.
    """
    try:
        return gitsafe.run(repo, ["check-ignore", "-q", str(path)]).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def no_remote_guard_set(repo: Path) -> bool:
    """Is the optional bare-push guard set in *repo*?  (`cf status` prints this.)

    Both halves have to be there: ``push.default nothing`` refuses a push that
    names no refspec, and ``remote.pushDefault`` pointing at a remote that does
    not exist refuses one that names a refspec but no remote.  Neither stops an
    explicit ``git push <remote> <ref>``, which is why this is a guard and the
    absence of a remote is still the control.
    """
    return (_config(repo, "remote.pushDefault") == NO_REMOTE_GUARD_REMOTE
            and _config(repo, "push.default") == "nothing")


def _install_no_remote_guard(repo: Path) -> None:
    """Set the bare-push guard in *repo*'s local config (idempotent)."""
    for key, value in (("remote.pushDefault", NO_REMOTE_GUARD_REMOTE),
                       ("push.default", "nothing")):
        res = subprocess.run(["git", "-C", str(repo), "config", "--local", key, value],
                             capture_output=True, text=True)
        if res.returncode != 0:
            log.warn(f"could not set {key} in {repo}: {res.stderr.strip()}")
            return
    log.ok(f"bare-push guard set in {repo}/.git/config (remote.pushDefault "
           f"{NO_REMOTE_GUARD_REMOTE}, push.default nothing)")


def _guard_note(repo: Path) -> None:
    """Say what the guard on *repo* is, and what it is not.

    Printed on every `cf init`, not only the first: the whole finding this
    answers is that a pre-push hook reads as enforcement to the next person, and
    the place they meet it is this output.
    """
    log.info("the pre-push hook is a REMINDER: `git push --no-verify` skips it and a fresh "
             "clone does not")
    log.info("  carry it. The control is that this repo has NO REMOTE -- `cf init` refuses "
             "one and `cf status`")
    log.info("  reports one that appears later.")
    if not no_remote_guard_set(repo):
        log.info(f"  `cf init {repo} --no-remote-guard` also makes a bare `git push "
                 f"--no-verify` fail.")


def _install_hook(repo: Path) -> None:
    """Install the pre-push guard in *repo* (never overwrites an existing hook)."""
    hook = repo / ".git" / "hooks" / "pre-push"
    state = hook_state(repo)
    if state == "no-hooks-dir":
        log.warn(f"{hook.parent} does not exist; no pre-push guard installed")
        return
    if state == "foreign":
        # This used to be log.detail, i.e. invisible at default verbosity, and
        # `cf init` still exited 0: the workspace looked guarded while somebody
        # else's hook was the only thing on the push path.
        log.warn_block(
            f"{hook} already exists and is NOT the cf guard; left untouched -- this "
            f"workspace has no cf pre-push reminder",
            [f"     inspect it, then either add the same check to it yourself, or move "
             f"it aside and re-run `cf init {repo}` (which installs the guard when the "
             f"path is free)"])
        return
    if state == "cf":
        log.detail(f"{hook} is already the cf guard; left untouched")
        return
    atomic_write(hook, PRE_PUSH_HOOK)
    hook.chmod(0o755)
    log.ok(f"installed {hook}")


def _init_git(root: Path, allow_nested: bool = False, allow_remote: bool = False,
              no_remote_guard: bool = False) -> None:
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
        top = git_toplevel(root)
        if top and top != root:
            # _refuse_nested_git() already refused the cases that must not happen.
            log.warn(f"{root} is inside {top}: guarding THAT repo instead of running git init")
            _install_hook(top)
            if no_remote_guard:
                _install_no_remote_guard(top)
            _append_ignore(top, root)
            _guard_note(top)
            return
        res = subprocess.run(["git", "init", "-q", str(root)], capture_output=True, text=True)
        if res.returncode != 0:
            log.warn(f"git init failed: {res.stderr.strip()}")
            return
        log.ok(f"git init {root} (no remote)")
    else:
        log.detail("git repo already present")
    remote_names = remotes(root)
    if remote_names and not allow_remote:
        # THIS REFUSAL is the control: workspace.yaml policy.allow_remote (false
        # by default) plus `cf init` refusing a workspace repo that has a remote.
        # The pre-push hook below is only a reminder -- `git push --no-verify`
        # skips it, a fresh clone does not carry it, and a remote added after
        # `cf init` never passes through here at all (`cf status` reports both
        # facts on every run, which is what catches that case).
        raise CfError(
            f"this workspace repo has a remote ({remote_names[0]}) and workspace.yaml sets "
            "policy.allow_remote: false",
            hint="book quotes and UNCHALLENGED findings live here; run "
                 f"`git -C {root} remote remove {remote_names[0]}`, or set policy.allow_remote: "
                 "true in workspace.yaml and take responsibility for every push")
    if remote_names:
        log.warn(f"this workspace repo has a remote ({remote_names[0]}) and policy.allow_remote "
                 "is true: book quotes and unchallenged findings must not be pushed")
    _install_hook(root)
    if no_remote_guard:
        _install_no_remote_guard(root)
    _guard_note(root)


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


def git_toplevel(path: Path) -> Path | None:
    """The repository *path* is inside, if any.

    Public because `cf status` needs it: a workspace created with
    ``--allow-nested`` has no ``.git`` of its own and the pre-push reminder lives
    in the ENCLOSING repo, so the repo whose remote matters is this one.

    Pinned through ``cflib.gitsafe`` for the reason :func:`check_ignore` is: not
    because ``rev-parse --show-toplevel`` runs ``core.fsmonitor`` (measured: it
    does not), but because *path* can come from a flag the operator typed, and the
    rule in this file is that no ``git`` reachable from an operator's path runs
    with the answering repository's own configuration.  A hung git means "no
    repo", which is what an unreadable one already meant.
    """
    try:
        res = gitsafe.run(path, ["rev-parse", "--show-toplevel"])
    except subprocess.TimeoutExpired:
        return None
    return Path(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else None
