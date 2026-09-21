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
(:func:`filter_pins`), because a command that compares working-tree content runs
``filter.<n>.clean`` and no fixed pin can blank a driver whose name the repo
chose.

plus ``GIT_CONFIG_NOSYSTEM=1`` (ignore ``/etc/gitconfig``),
``GIT_OPTIONAL_LOCKS=0`` (never take a lock in someone else's repo) and
``GIT_TERMINAL_PROMPT=0``.

The user's own ``~/.gitconfig`` is deliberately still read: it is the user's
machine.  What is neutralised is the part of the configuration the *audited
repository* controls -- with one exception, because git resolves a CONTENT
filter by the name an attribute in the repo gives it, not by who configured the
driver: :func:`filter_pins` blanks every named driver in every scope, the user's
own included.  Its docstring says what that costs (git-lfs) and why a read-only
audit pays it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

__all__ = ["SAFE_CONFIG", "SAFE_ENV", "argv", "env", "filter_pins", "run"]

#: ``-c key=value`` pairs, flattened; every one of these is a program git would
#: otherwise run on behalf of the repository being read.  Fixed keys only: the
#: content filters are named by the repo, so they are pinned per call in
#: :func:`filter_pins`.
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


def filter_pins(repo: str | Path) -> list[str]:
    """``-c filter.<n>.clean=`` … for every content filter *repo* names.

    :data:`SAFE_CONFIG` stops the index-refresh hook; it does not stop git's
    content filters, which are the other program a repository can hand a reader.
    Any command that compares working-tree content runs ``filter.<n>.clean`` over
    a file whose index entry is stat-stale -- ``git status --porcelain``, the call
    ``recheck/pin.py`` makes, does -- so a companion carrying ``filter.x.clean``
    in ``.git/config`` and ``*.lean filter=x`` in ``.git/info/attributes``
    executed its own program on a tree this skill only ever reads.  Verified.

    The exposure is the same one the fsmonitor pins were written for and no
    wider: ``git clone`` copies neither of those two files, so it takes a tree
    delivered with its ``.git/`` intact (a tarball, an rsync, a handover).

    Blanked by NAME and in every scope, because git resolves a driver by the name
    an attribute gives it: ``.git/config.worktree`` is the repository's own and is
    not in ``--local``, and an attribute may name a driver the user's
    ``~/.gitconfig`` defines (git-lfs, typically -- one consequence is that an
    LFS file whose index entry is stat-stale may read as modified, which a
    read-only audit prefers to an execve).  ``git config --list`` runs no filter
    itself and resolves ``include.path``, so a driver reached through an include
    is covered; an attribute naming a driver nothing defines was already a no-op.
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
    return [arg for key in names for arg in ("-c", key + "=")]


def argv(repo: str | Path, *args: str) -> list[str]:
    """``git -C <repo> <safe config> <args…>`` (config must precede the subcommand).

    Reads *repo*'s configuration to build the filter pins, so this is one cheap
    ``git config`` call, not a pure string join.
    """
    return ["git", "-C", str(repo), *SAFE_CONFIG, *filter_pins(repo), *args]


def env() -> dict[str, str]:
    return {**os.environ, **SAFE_ENV}


def run(repo: str | Path, args: Sequence[str], *, input: str | None = None,
        timeout: int = 60) -> subprocess.CompletedProcess:
    """Run one read-only git command inside *repo*, with the boundary pinned.

    Raises the same exceptions ``subprocess.run`` does; callers decide what a
    failure means (every caller in this skill treats it as "no information").
    """
    return subprocess.run(argv(repo, *args), input=input, capture_output=True,
                          text=True, timeout=timeout, env=env())
