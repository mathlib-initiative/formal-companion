"""Error types for the cf CLI.

New code (no original): the recovered scripts died with tracebacks; the skill
needs one error type the CLI can render as a single line.
"""
from __future__ import annotations


class CfError(Exception):
    """A user-facing error: cf.py prints ``error: <msg>`` and exits ``exit_code``.

    ``hint`` is an optional second line telling the user what to do next.
    """

    exit_code = 2

    def __init__(self, message: str, hint: str | None = None, exit_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if exit_code is not None:
            self.exit_code = exit_code

    def render(self) -> str:
        out = f"error: {self.message}"
        if self.hint:
            out += f"\nhint:  {self.hint}"
        return out


class WorkspaceError(CfError):
    """workspace.yaml is missing, malformed, or inconsistent."""


class SourceError(CfError):
    """A source is unknown, unprepared, or pinned to the wrong bytes."""


class DiscoveryError(CfError):
    """`cf onboard` could not decide something it refuses to guess at.

    Discovery replaces typed metadata (docs/design/multi-source-model.md R2), so
    every thing it cannot establish -- the adapter kind, the page offset, which
    edition a repo's anchors follow -- is a refusal that names the evidence and
    the one flag that settles it, never a silent default.
    """


class StateError(CfError):
    """companions/<name>/state.json is missing or malformed."""


class ItemGrammarError(CfError):
    """A source item id does not match the strict grammar (see cflib.items).

    The grammar is per SOURCE: the kind list comes from ``sources.<id>.kinds``,
    so the same string can be a valid item of one source and junk in another.
    """

    def __init__(self, raw: str, reason: str, hint: str | None = None):
        self.raw = raw
        self.reason = reason
        super().__init__(f"unparseable book item {raw!r} ({reason})", hint=hint)


class AdapterError(CfError):
    """An adapter could not be loaded or failed its dry run."""


class GateError(CfError):
    """A mechanical gate refused an artifact."""
