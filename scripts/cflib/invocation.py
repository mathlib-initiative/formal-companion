"""The one place a copy-pasteable ``cf ...`` invocation is spelled.

New code (no original), for the defect in `docs/design/stage_map.md`'s stage 7:
two commands one step apart printed two different spellings of the SAME next
step.  ``cf route`` printed ``cf adjudicate --workspace <ws> ...`` -- which runs
-- and ``cf adjudicate`` printed ``python3 scripts/cf.py adjudicate --workspace
<ws> ...``, a path relative to the SKILL checkout handed to an operator standing
in the WORKSPACE, where there is no ``scripts/`` at all.  Pasting it answered
"can't open file <ws>/scripts/cf.py".

A hint is not only printed: ``route`` writes the same string into
``ledger/routed/findings.<name>.jsonl`` and into the owner-message draft, so a
second spelling is a second thing to keep true.  Build the invocation here and
the two cannot drift.
"""
from __future__ import annotations

import os
import shlex


def adjudicate_command(workspace, companion: str, *tail: str) -> str:
    """``cf adjudicate`` for *companion*, with the workspace RESOLVED.

    ``tail`` is whatever this caller's step needs after ``--companion <name>``
    (``--emit-args``, ``--challenge-only --emit-args``, a ``--transcribe`` pair).

    With no workspace to resolve the flag is left OFF rather than filled with a
    literal placeholder: ``cf`` then falls back to ``$CF_WORKSPACE`` or the
    current directory, which is what ``cf --help`` promises, so the command
    still runs.

    The path and the name are SHELL-QUOTED here, once, rather than at each call
    site.  A workspace directory called ``my audit`` made every one of these
    strings unpasteable -- ``cf: error: unrecognized arguments: audit/ws`` --
    and this one is not only printed, so that broken command was written into
    ``ledger/routed/findings.<name>.jsonl`` and into the draft addressed to the
    repository's owner.
    """
    where = " --workspace %s" % shlex.quote(os.path.abspath(workspace)) if workspace else ""
    parts = ["cf adjudicate%s --companion %s" % (where, shlex.quote(str(companion)))]
    parts.extend(str(part) for part in tail if part)
    return " ".join(parts)
