"""Read a companion repository's git metadata without running code it chose.

New code (design S4.2 / references/onboarding-contract.md: "a companion tree is
read as DATA").

A companion repo is an untrusted tree: `cf` clones nothing, builds nothing and
writes nothing there, and the whole audit rests on the repo being *read*.

(One documented exception, and it is not run by `cf`: ``scripts/detect/dump_deps.sh``
is a command line `cf detect-proxies --lean` PRINTS for an operator to run, and
``lake env lean --run`` there executes the companion's lakefile and toolchain as
the invoking user.  Its header says so; it belongs only against a repo you trust
or inside a sandbox.)

Plain ``git -C <repo> …`` breaks the read-only boundary too.  Several porcelain
commands refresh the index first, and an index refresh runs ``core.fsmonitor``
-- a command named in the repository's own ``.git/config``.  ``git check-ignore
--stdin`` is one of them, so the first thing `cf onboard <repo>` did to a tree it
had never seen was to execute a program that tree had chosen.

Every git call this skill makes against a companion goes through :func:`run`,
which pins the config keys that would otherwise hand a repo an execve:

* ``core.fsmonitor=``      -- the index-refresh hook (verified: this pin alone
  stops THAT hook, and nothing else; the content filters below are a second
  route and need their own)
* ``core.hooksPath=/dev/null`` -- no repo hook script is reachable
* ``diff.external=``       -- no external diff driver
* ``core.pager=cat``       -- no pager program, no ``less`` config games
* ``core.sshCommand=``/``core.askPass=`` -- nothing that could dial out or prompt

plus, per call and by name, every content filter the repository names
(:func:`filter_env`), because a command that compares working-tree content runs
``filter.<n>.clean`` and no fixed pin can blank a driver whose name the repo
chose.  Those blanks travel in the ENVIRONMENT, not in ``-c``: git splits a
``-c`` argument at the FIRST ``=``, so ``-c 'filter.a=b.clean='`` sets
``filter.a`` to ``b.clean=`` and leaves the driver a repo called ``a=b`` armed.

plus ``GIT_CONFIG_NOSYSTEM=1`` (ignore ``/etc/gitconfig``),
``GIT_OPTIONAL_LOCKS=0`` (never take a lock in someone else's repo) and
``GIT_TERMINAL_PROMPT=0``.

The user's own ``~/.gitconfig`` is deliberately still read: it is the user's
machine.  What is neutralised is the part of the configuration the *audited
repository* controls -- with one exception, because git resolves a CONTENT
filter by the name an attribute in the repo gives it, not by who configured the
driver: :func:`filter_env` blanks every named driver in every scope, the user's
own included.  Its docstring says what that costs (git-lfs) and why a read-only
audit pays it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

__all__ = ["SAFE_CONFIG", "SAFE_ENV", "argv", "env", "filter_env", "filter_names", "run"]

#: ``-c key=value`` pairs, flattened; every one of these is a program git would
#: otherwise run on behalf of the repository being read.  Fixed keys only: the
#: content filters are named by the repo, so they are pinned per call in
#: :func:`filter_env`.
SAFE_CONFIG: tuple[str, ...] = (
    "-c", "core.fsmonitor=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "diff.external=",
    "-c", "core.pager=cat",
    "-c", "core.sshCommand=",
    "-c", "core.askPass=",
)

#: environment overrides applied on top of ``os.environ``
SAFE_ENV: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


#: the three filter hooks; ``smudge`` never runs on a read, but a pin costs
#: nothing and this list is what a reader checks against ``git help attributes``
_FILTER_HOOKS = ("clean", "process", "smudge")


def filter_names(repo: str | Path) -> list[str]:
    """Every ``filter.<n>.clean``/``.process``/``.smudge`` key *repo* resolves.

    ``git config --list`` runs no filter itself and resolves ``include.path``, so
    a driver reached through an include is covered; an attribute naming a driver
    nothing defines was already a no-op.
    """
    try:
        got = subprocess.run(["git", "-C", str(repo), *SAFE_CONFIG,
                              "config", "--list", "--name-only"],
                             capture_output=True, text=True, timeout=30,
                             check=False, env=env())
    except (OSError, subprocess.SubprocessError):
        return []          # not a repo, or no git: the caller's command says so
    names: list[str] = []
    for line in (got.stdout or "").splitlines():
        key = line.strip()
        if key.startswith("filter.") and key.rsplit(".", 1)[-1] in _FILTER_HOOKS:
            if key not in names:
                names.append(key)
    return names


def filter_env(repo: str | Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """``GIT_CONFIG_COUNT``/``_KEY_i``/``_VALUE_i`` blanking every filter *repo* names.

    :data:`SAFE_CONFIG` stops the index-refresh hook; it does not stop git's
    content filters, which are the other program a repository can hand a reader.
    Any command that compares working-tree content runs ``filter.<n>.clean`` over
    a file whose index entry is stat-stale -- ``git status --porcelain``, the call
    ``recheck/pin.py`` makes, does -- so a companion carrying ``filter.x.clean``
    in ``.git/config`` and ``*.lean filter=x`` in ``.git/info/attributes``
    executed its own program on a tree this skill only ever reads.  Verified.

    The blanks are ENVIRONMENT pairs, not ``-c`` arguments, because ``-c`` splits
    at the FIRST ``=`` and a repository chooses the driver's name: against
    ``[filter "a=b"] clean = <payload>`` plus ``*.lean filter=a=b``,
    ``-c 'filter.a=b.clean='`` sets ``filter.a`` to ``b.clean=``, the real driver
    keeps its payload and the read executes it.  ``GIT_CONFIG_KEY_i`` carries the
    whole key, so there is nothing to split.  They are added ON TOP of any count
    the caller's own environment already carries, so a ``GIT_CONFIG_*`` the user
    set for their own reasons is extended rather than dropped.

    The exposure is the same one the fsmonitor pins were written for and no
    wider: ``git clone`` copies neither of those two files, so it takes a tree
    delivered with its ``.git/`` intact (a tarball, an rsync, a handover).

    Blanked by NAME and in every scope, because git resolves a driver by the name
    an attribute gives it: ``.git/config.worktree`` is the repository's own and is
    not in ``--local``, and an attribute may name a driver the user's
    ``~/.gitconfig`` defines (git-lfs, typically -- one consequence is that an
    LFS file whose index entry is stat-stale may read as modified, which a
    read-only audit prefers to an execve).
    """
    names = filter_names(repo)
    if not names:
        return {}
    have = os.environ if base is None else base
    try:
        start = max(0, int(have.get("GIT_CONFIG_COUNT") or 0))
    except (TypeError, ValueError):
        start = 0
    out = {"GIT_CONFIG_COUNT": str(start + len(names))}
    for i, key in enumerate(names, start):
        out[f"GIT_CONFIG_KEY_{i}"] = key
        out[f"GIT_CONFIG_VALUE_{i}"] = ""
    return out


def argv(repo: str | Path, *args: str) -> list[str]:
    """``git -C <repo> <safe config> <args…>`` (config must precede the subcommand).

    The content filters are blanked in the environment (:func:`filter_env`), not
    here, so this argv is a pure string join: a caller that builds it must run it
    with :func:`env` of the same repo.
    """
    return ["git", "-C", str(repo), *SAFE_CONFIG, *args]


def env(repo: str | Path | None = None) -> dict[str, str]:
    """The environment a read of *repo* runs in: :data:`SAFE_ENV` plus its filters.

    With no repo it is the fixed half alone, which is what the one ``git config``
    call :func:`filter_names` makes needs (that call runs no filter itself).
    """
    out = {**os.environ, **SAFE_ENV}
    if repo is not None:
        out.update(filter_env(repo, out))
    return out


def run(repo: str | Path, args: Sequence[str], *, input: str | None = None,
        timeout: int = 60) -> subprocess.CompletedProcess:
    """Run one read-only git command inside *repo*, with the boundary pinned.

    Raises the same exceptions ``subprocess.run`` does; callers decide what a
    failure means (every caller in this skill treats it as "no information").
    """
    return subprocess.run(argv(repo, *args), input=input, capture_output=True,
                          text=True, timeout=timeout, env=env(repo))
