#!/usr/bin/env python3
"""cf -- the formal-companion CLI.

New code (CLAUDE.md S5 is the contract for adding one).  Run it through the
shim on PATH, or by path:

    cf status
    python3 <skill>/scripts/cf.py status
    python3 <skill>/scripts/cf.py --workspace <workspace> status

Commands live one-per-module in ``scripts/cfcmd/``; each module exposes
``register(subparsers)`` and ``run(args)``.  cf.py discovers them with
pkgutil, so a new stage is a new file and nothing else.  A command module that
fails to import is reported but does not stop the others from working.

``main()`` RAISES CfError, for a library caller that wants the exception;
``cli()`` is the process entry point and renders it as ``error:``/``hint:`` with
the error's own exit code.  Every entry point -- ``__main__``, ``bin/cf`` and the
``cf`` console script -- goes through ``cli()``, so none of them can print a
traceback where the CLI promises one line.
"""
from __future__ import annotations

import sys

#: The floor README.md and CLAUDE.md S3 state, and the versions CI runs.
#: Enforced here because it was enforced nowhere: the tree happens to run on 3.9
#: today, so the day someone writes `match` or imports tomllib an older
#: interpreter fails with a SyntaxError deep inside a fleet worker instead of
#: refusing in one line at startup.
MIN_PYTHON = (3, 11)


def python_too_old(version_info=None) -> str:
    """The one-sentence refusal for an interpreter below :data:`MIN_PYTHON`.

    Empty string when the interpreter is new enough.  Written in 3.6-compatible
    syntax and called before anything else is imported, so the refusal survives
    on the interpreter it is refusing.
    """
    found = tuple(sys.version_info if version_info is None else version_info)
    if found >= MIN_PYTHON:
        return ""
    return ("error: cf needs Python %d.%d or newer, but this is Python %d.%d; "
            "re-run it with a newer interpreter."
            % (MIN_PYTHON[0], MIN_PYTHON[1], found[0], found[1]))


if python_too_old():  # pragma: no cover - the interpreter running the tests passes
    sys.stderr.write(python_too_old() + "\n")
    raise SystemExit(2)

import argparse  # noqa: E402
import importlib  # noqa: E402
import os  # noqa: E402
import pkgutil  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cflib import __version__, log  # noqa: E402
from cflib.errors import CfError  # noqa: E402

EPILOG = """\
workspace directory:
  --workspace <dir>  the workspace to act on (default: $CF_WORKSPACE, else the
                     current directory or the nearest ancestor holding
                     workspace.yaml).  Create one with `cf init <dir>`.

A workspace holds N COMPANIONS and M SOURCES; a companion is audited against a
LIST of sources, and a differential is possible exactly where two companions'
source lists overlap.  Nothing about a source is typed in: `cf onboard <repo>`
discovers it, fingerprints the source and infers the page offset, the item
kinds, the numbering depth and the errata page.

typical program:
  cf doctor                             # git, poppler and curl on PATH?
  cf init <workspace>
  cf onboard <path to a companion repo>
  cf onboard <path to the other companion repo>
  cf sources list
  cf pair

Run `cf <command> --help` for a command's options.
"""


class _BrokenCommand:
    """Stand-in for a cfcmd module that failed to import."""

    def __init__(self, name: str, exc: BaseException):
        self.name = name
        self.exc = exc

    def run(self, args) -> int:
        raise CfError(f"command '{self.name}' failed to import: {self.exc}",
                      hint="run with -v for the traceback; the module is in scripts/cfcmd/")


def discover_commands(subparsers) -> dict:
    """Import every ``cfcmd.*`` module and let it register its subcommands.

    Returns {subcommand name: module}.  Import failures register a stub so the
    CLI still runs while a sibling stage is being written.
    """
    import cfcmd

    mapping: dict[str, object] = {}
    for info in sorted(pkgutil.iter_modules(cfcmd.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        before = set(subparsers.choices)
        try:
            module = importlib.import_module(f"cfcmd.{info.name}")
        except Exception as exc:  # noqa: BLE001 - a broken sibling must not break the CLI
            log.detail(f"cfcmd.{info.name}: {traceback.format_exc()}")
            if info.name not in subparsers.choices:
                subparsers.add_parser(info.name, help=f"(unavailable: {exc.__class__.__name__})")
                mapping[info.name] = _BrokenCommand(info.name, exc)
            continue
        register = getattr(module, "register", None)
        if register is None:
            log.detail(f"cfcmd.{info.name} has no register(subparsers); skipped")
            continue
        try:
            register(subparsers)
        except Exception as exc:  # noqa: BLE001
            log.detail(f"cfcmd.{info.name}.register failed: {traceback.format_exc()}")
            if info.name not in subparsers.choices:
                subparsers.add_parser(info.name, help=f"(unavailable: {exc.__class__.__name__})")
                mapping[info.name] = _BrokenCommand(info.name, exc)
            continue
        for name in set(subparsers.choices) - before:
            mapping[name] = module
    return mapping


def build_parser():
    """The argument parser and the {command: module} dispatch table."""
    parser = argparse.ArgumentParser(
        prog="cf",
        description="Audit Lean book companions against their sources (findings only).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"cf {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    mapping = discover_commands(subparsers)
    return parser, mapping


def _global_options(argv: list[str]):
    """Pull the options every command shares out of argv, wherever they sit."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--workspace", default=None)
    pre.add_argument("-v", "--verbose", action="count", default=0)
    pre.add_argument("-q", "--quiet", action="store_true")
    known, rest = pre.parse_known_args(argv)
    if known.workspace is None:
        known.workspace = os.environ.get("CF_WORKSPACE") or None
    return known, rest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    globals_, rest = _global_options(argv)
    log.set_level(0 if globals_.quiet else min(2, 1 + globals_.verbose))

    parser, mapping = build_parser()
    if not rest or rest[0] in {"-h", "--help"}:
        parser.print_help()
        return 0 if rest else 1
    args = parser.parse_args(rest)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    args.workspace = globals_.workspace
    args.verbose = globals_.verbose
    module = mapping.get(args.command)
    if module is None:  # pragma: no cover - argparse rejects unknown commands first
        log.error(f"error: no module registered for command {args.command!r}")
        return 2
    run = getattr(module, "run", None)
    if run is None:
        log.error(f"error: cfcmd module for {args.command!r} has no run(args)")
        return 2
    result = run(args)
    return int(result or 0)


def cli(argv: list[str] | None = None) -> int:
    """Process entry point: run a command and render a CfError as one line.

    This lived in the ``if __name__ == "__main__"`` block, where only ``python3
    scripts/cf.py`` could reach it -- a ``cf`` on PATH or a console script
    calling ``main()`` would print a traceback for every refusal the CLI
    promises to report as ``error:`` plus ``hint:``.  ``main()`` still raises, so
    a library caller (and the tests that assert on CfError) is unaffected.
    """
    try:
        return main(argv)
    except CfError as exc:
        log.error(exc.render())
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover
        log.error("interrupted")
        return 130
    except BrokenPipeError:  # pragma: no cover
        return 0


if __name__ == "__main__":
    sys.exit(cli())
