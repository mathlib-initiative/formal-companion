"""Tiny logger and table renderer shared by every cf command.

New code (no original). Rules: human output on stdout, diagnostics on stderr,
no colour unless the stream is a tty and NO_COLOR is unset.
"""
from __future__ import annotations

import os
import sys
from typing import Iterable, Sequence

_LEVEL = 1  # 0 quiet, 1 normal, 2 verbose

_COLORS = {"warn": "33", "error": "31", "ok": "32", "dim": "90"}


def set_level(level: int) -> None:
    """0 = quiet (errors only), 1 = normal, 2 = verbose."""
    global _LEVEL
    _LEVEL = level


def level() -> int:
    return _LEVEL


def _color(tag: str, text: str, stream) -> str:
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return text
    code = _COLORS.get(tag)
    return f"\033[{code}m{text}\033[0m" if code else text


def info(msg: str = "") -> None:
    if _LEVEL >= 1:
        print(msg)


def detail(msg: str) -> None:
    """Verbose-only diagnostic (stderr)."""
    if _LEVEL >= 2:
        print(_color("dim", f"  {msg}", sys.stderr), file=sys.stderr)


def ok(msg: str) -> None:
    if _LEVEL >= 1:
        print(_color("ok", f"ok   {msg}", sys.stdout))


def warn(msg: str) -> None:
    if _LEVEL >= 1:
        print(_color("warn", f"warn {msg}", sys.stderr), file=sys.stderr)


def error(msg: str) -> None:
    print(_color("error", msg, sys.stderr), file=sys.stderr)


def format_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Render a fixed-width table. Cells are str()-ed; None becomes '-'."""
    body = [[("-" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in body:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()
    out = [line(list(headers)), line(["-" * w for w in widths])]
    out.extend(line(row) for row in body)
    return "\n".join(out)
