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

Plain ``git -C <repo> …`` breaks the read-only boundary too.  Several porcelain commands refresh the index
first, and an index refresh runs ``core.fsmonitor`` -- a command named in the
repository's own ``.git/config``.  ``git check-ignore --stdin`` is one of them,
so the first thing `cf onboard <repo>` did to a tree it had never seen was to
execute a program that tree had chosen.

Every git call this skill makes against a companion goes through :func:`run`,
which pins the config keys that would otherwise hand a repo an execve:

* ``core.fsmonitor=``      -- the index-refresh hook (verified: this alone stops it)
* ``core.hooksPath=/dev/null`` -- no repo hook script is reachable
* ``diff.external=``       -- no external diff driver
* ``core.pager=cat``       -- no pager program, no ``less`` config games
* ``core.sshCommand=``/``core.askPass=`` -- nothing that could dial out or prompt

plus ``GIT_CONFIG_NOSYSTEM=1`` (ignore ``/etc/gitconfig``),
``GIT_OPTIONAL_LOCKS=0`` (never take a lock in someone else's repo) and
``GIT_TERMINAL_PROMPT=0``.

The user's own ``~/.gitconfig`` is deliberately still read: it is the user's
machine.  What is neutralised is the part of the configuration the *audited
repository* controls.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

__all__ = ["SAFE_CONFIG", "SAFE_ENV", "argv", "env", "run"]

#: ``-c key=value`` pairs, flattened; every one of these is a program git would
#: otherwise run on behalf of the repository being read.
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


def argv(repo: str | Path, *args: str) -> list[str]:
    """``git -C <repo> <safe config> <args…>`` (config must precede the subcommand)."""
    return ["git", "-C", str(repo), *SAFE_CONFIG, *args]


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
