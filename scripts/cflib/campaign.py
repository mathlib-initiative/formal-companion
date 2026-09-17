"""TEMPORARY COMPATIBILITY SHIM -- delete me.

``cflib.campaign`` was replaced by :mod:`cflib.workspace`
(docs/design/multi-source-model.md R1: one campaign per book -> one companion per
list of sources).  The design says plainly that no alias is kept, and none is:
the names below are the WORKSPACE names, re-exported under the module path the
not-yet-migrated packages still import, so that

    from cflib.campaign import from_args

resolves at import time while ADAPTERS / STAGES-A / STAGES-B are being migrated.
Nothing here restores the old *behaviour*: ``Campaign`` IS ``Workspace``, it
reads ``workspace.yaml``, and every ``book_sha`` / ``book_id`` / ``offset``
attribute a caller still reaches for is gone, so a module that has not been
migrated fails loudly at the line that matters instead of at its import.

WHEN THIS FILE GOES: the moment the last module in the grep below stops naming
it.  It must not survive the STAGES-B package.

    grep -rn 'cflib.campaign\\|cflib import campaign' $(git ls-files)
"""
from __future__ import annotations

from .workspace import (  # noqa: F401
    WORKSPACE_FILE, WORKSPACE_VERSION, Workspace, Workspace as Campaign,
    find_workspace_root, from_args,
)

__all__ = ["Campaign", "Workspace", "find_workspace_root", "from_args",
           "WORKSPACE_FILE", "WORKSPACE_VERSION"]
