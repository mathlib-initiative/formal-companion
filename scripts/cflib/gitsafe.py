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

plus, per call and by name, every content filter the repository names, because a
command that compares working-tree content runs ``filter.<n>.clean`` and no
fixed pin can blank a driver whose name the repo chose.  Those blanks travel
BOTH ways, and neither channel covers what the other does: as environment pairs
(:func:`filter_env`), which carry a whole key, because git splits a ``-c``
argument at the FIRST ``=`` and ``-c 'filter.a=b.clean='`` sets ``filter.a`` to
``b.clean=`` and leaves the driver a repo called ``a=b`` armed; and as ``-c``
blanks (:func:`filter_pin_args`) for every name without an ``=``, because
``GIT_CONFIG_COUNT`` arrived in git 2.31 and an older git ignores it in silence
-- no error, no exit code, the driver simply runs.  This file goes to a run host
whose git version nothing here checks (``references/runbook-server.md`` §2), and
Debian 11 ships 2.30.2.

Which leaves one square where the two channels do not overlap: an ``=``-named
driver on a git that ignores the environment pairs has no blank at all.  There
:func:`run` STOPS -- :func:`unexpressible_filter` names the driver and
:func:`git_version` the git -- and returns a refusal instead of a read, because
the alternative is to run the repository's program and report the tree clean.
A git whose version does not parse counts as old: a version we cannot read is
not one a safety floor may rest on.

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

import functools
import os
import re
import subprocess
from pathlib import Path
from typing import Sequence

__all__ = ["CONFIG_ENV_SINCE", "REFUSED", "SAFE_CONFIG", "SAFE_ENV", "argv", "env",
           "filter_env", "filter_names", "filter_pin_args", "git_version",
           "honours_config_env", "refusal", "run", "unexpressible_filter"]

#: ``-c key=value`` pairs, flattened; every one of these is a program git would
#: otherwise run on behalf of the repository being read.  Fixed keys only: the
#: content filters are named by the repo, so they are pinned per call, in the
#: environment (:func:`filter_env`) and in ``-c`` (:func:`filter_pin_args`).
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

#: the git that learned ``GIT_CONFIG_COUNT``/``_KEY_i``/``_VALUE_i``.  Before it
#: those variables are ignored in silence, so the environment channel does not
#: exist there and a name ``-c`` cannot express has no blank at all.
CONFIG_ENV_SINCE: tuple[int, int] = (2, 31)

#: what :func:`run` reports when it refuses to read at all: a non-zero status and
#: no output, which is what every caller in this skill already reads as "no
#: information" (``recheck/pin.py`` records a pin that says so, ``cf onboard``
#: records no commit).  126 is the shell's "found but not executable" and no git
#: command returns it, so a caller that wants to tell them apart can.
REFUSED = 126


@functools.lru_cache(maxsize=1)
def git_version() -> tuple[int, ...] | None:
    """``git --version`` as a tuple of integers, asked once per process.

    ``None`` when there is no git on PATH, when the call fails, or when the
    answer does not parse (``git version 2.39.5 (Apple Git-154)`` does).  Every
    caller here reads ``None`` as "older than anything we may rely on": a version
    that cannot be read is not one a safety floor may be built on.

    Carries :data:`SAFE_CONFIG` although ``--version`` reads no repository: the
    rule in this file is that no ``git`` it spells runs without the boundary, and
    an exemption is how the next call loses it.
    """
    try:
        got = subprocess.run(["git", *SAFE_CONFIG, "--version"], capture_output=True,
                             text=True, timeout=30, check=False,
                             env={**os.environ, **SAFE_ENV})
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", got.stdout or "")
    if got.returncode != 0 or not found:
        return None
    return tuple(int(part) for part in found.groups() if part is not None)


def honours_config_env() -> bool:
    """Does the git on PATH read ``GIT_CONFIG_COUNT`` and its pairs?"""
    version = git_version()
    return version is not None and version >= CONFIG_ENV_SINCE


def unexpressible_filter(names: Sequence[str]) -> str | None:
    """The first driver in *names* that NEITHER channel of the blanking reaches.

    A name carrying an ``=`` is invisible to ``-c`` (git splits at the first
    ``=``), so the environment pairs are its only blank -- and on a git before
    :data:`CONFIG_ENV_SINCE` those pairs are ignored in silence.  There the
    driver is armed whatever this module passes, so the read must stop: the
    alternative is to execute the repository's own program while reporting
    nothing.  ``None`` on a git that honours the pairs, which is the ordinary
    case and leaves behaviour unchanged.
    """
    if honours_config_env():
        return None
    return next((key for key in names if "=" in key), None)


def refusal(repo: str | Path, key: str) -> str:
    """Why :func:`run` read nothing, in the shape the callers' messages take."""
    version = git_version()
    shown = ".".join(str(n) for n in version) if version else "unreadable"
    return (f"refusing to read {repo}: it names the content filter {key!r}, whose "
            f"'=' no `git -c` blank can express, and this git ({shown}) is older "
            f"than the {'.'.join(str(n) for n in CONFIG_ENV_SINCE)} that reads the "
            f"GIT_CONFIG_* blanking which can -- so the read would run that driver. "
            f"Install git {'.'.join(str(n) for n in CONFIG_ENV_SINCE)} or later "
            f"(`git --version`), or rename the driver in the repository's config.")


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


def filter_pin_args(names: Sequence[str]) -> list[str]:
    """``-c <key>=`` for every name in *names* that ``-c`` can express.

    The second half of the blanking, and the one that survives an old git.
    ``GIT_CONFIG_COUNT`` and its ``_KEY_i``/``_VALUE_i`` pairs (:func:`filter_env`)
    are what reach a driver whose name carries an ``=``, which ``-c`` splits at;
    they are also what a git before 2.31 ignores without a word, so on a run host
    carrying Debian 11's 2.30.2 the env pairs alone leave every driver armed.
    ``-c`` is as old as the fixed pins above and covers every name but the ``=``
    ones, so both go on every call: each is the floor the other does not have.
    An ``=`` name on a git that reads neither is the square with no floor at all,
    and :func:`unexpressible_filter` stops the read there rather than pass a
    blank that does nothing.
    """
    return [arg for key in names if "=" not in key for arg in ("-c", key + "=")]


def _filter_pairs(names: Sequence[str], have: dict[str, str]) -> dict[str, str]:
    """:func:`filter_env`'s pairs, from names already discovered."""
    if not names:
        return {}
    try:
        start = max(0, int(have.get("GIT_CONFIG_COUNT") or 0))
    except (TypeError, ValueError):
        start = 0
    out = {"GIT_CONFIG_COUNT": str(start + len(names))}
    for i, key in enumerate(names, start):
        out[f"GIT_CONFIG_KEY_{i}"] = key
        out[f"GIT_CONFIG_VALUE_{i}"] = ""
    return out


def filter_env(repo: str | Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """``GIT_CONFIG_COUNT``/``_KEY_i``/``_VALUE_i`` blanking every filter *repo* names.

    :data:`SAFE_CONFIG` stops the index-refresh hook; it does not stop git's
    content filters, which are the other program a repository can hand a reader.
    Any command that compares working-tree content runs ``filter.<n>.clean`` over
    a file whose index entry is stat-stale -- ``git status --porcelain``, the call
    ``recheck/pin.py`` makes, does -- so a companion carrying ``filter.x.clean``
    in ``.git/config`` and ``*.lean filter=x`` in ``.git/info/attributes``
    executed its own program on a tree this skill only ever reads.  Verified.

    These pairs are why the blanking is not ``-c`` alone: ``-c`` splits at the
    FIRST ``=`` and a repository chooses the driver's name, so against
    ``[filter "a=b"] clean = <payload>`` plus ``*.lean filter=a=b``,
    ``-c 'filter.a=b.clean='`` sets ``filter.a`` to ``b.clean=``, the real driver
    keeps its payload and the read executes it.  ``GIT_CONFIG_KEY_i`` carries the
    whole key, so there is nothing to split.  They are added ON TOP of any count
    the caller's own environment already carries, so a ``GIT_CONFIG_*`` the user
    set for their own reasons is extended rather than dropped.  They are not the
    blanking either: :func:`filter_pin_args` is the half an old git still reads.

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
    return _filter_pairs(filter_names(repo), os.environ if base is None else base)


def _argv(repo: str | Path, args: Sequence[str], names: Sequence[str]) -> list[str]:
    """:func:`argv`, from names already discovered."""
    return ["git", "-C", str(repo), *SAFE_CONFIG, *filter_pin_args(names), *args]


def argv(repo: str | Path, *args: str) -> list[str]:
    """``git -C <repo> <safe config> <args…>`` (config must precede the subcommand).

    Reads *repo*'s configuration for the ``-c`` half of the filter blanks, so this
    is one cheap ``git config`` call and not a pure string join.  It is only half:
    a caller that builds the argv itself must run it with :func:`env` of the same
    repo, which carries the pairs that reach an ``=``-named driver.
    """
    return _argv(repo, args, filter_names(repo))


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

    The repository's filter drivers are discovered ONCE here and spliced into both
    channels from that one list -- the ``-c`` blanks and the environment pairs --
    so a call costs one ``git config --list``, not two.

    A driver neither channel reaches (:func:`unexpressible_filter`) stops the read
    instead: the result is a :data:`REFUSED` status, no stdout and
    :func:`refusal` on stderr, which is the "no information" every caller here
    already handles for a git that fails -- and the one answer that cannot end
    with the repository's program having run.

    Raises the same exceptions ``subprocess.run`` does; callers decide what a
    failure means (every caller in this skill treats it as "no information").
    """
    names = filter_names(repo)
    blocked = unexpressible_filter(names)
    if blocked is not None:
        return subprocess.CompletedProcess(_argv(repo, args, names), REFUSED, "",
                                           refusal(repo, blocked))
    environ = {**os.environ, **SAFE_ENV}
    environ.update(_filter_pairs(names, environ))
    return subprocess.run(_argv(repo, args, names), input=input, capture_output=True,
                          text=True, timeout=timeout, env=environ)
