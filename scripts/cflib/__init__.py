"""Shared library for the formal-companion CLI.

Import from here, not from sibling stage packages:

    from cflib import items, vocab, state
    from cflib.workspace import Workspace
    from cflib.errors import CfError

Every module is stdlib-only (PyYAML is used when importable, with a fallback
reader in :mod:`cflib.yamlio`).  Nothing here touches the network except
:mod:`cflib.discovery`, and only behind an explicit ``--download``; the errata
fetch lives in ``sources/fetch_errata.py``, and `cf onboard` runs it without
asking only for a URL a CONFIRMED registry entry supplies (a URL harvested from
the companion repo needs ``--fetch-errata`` or ``policy.allow_errata_fetch``).
Git is only ever run against a companion tree through :mod:`cflib.gitsafe`,
which pins the config keys that would otherwise let that tree name a program.
"""
from __future__ import annotations

__version__ = "0.2.0"

from . import (  # noqa: F401
    discovery, gitsafe, hashing, inference, items, jsonio, log, paths, registry,
    state, templates, vocab, yamlio,
)
from .errors import (  # noqa: F401
    AdapterError, CfError, DiscoveryError, GateError, ItemGrammarError, SourceError,
    StateError, WorkspaceError,
)
from .paths import WorkspacePaths, skill_root, templates_dir  # noqa: F401
from .registry import Registry  # noqa: F401
from .workspace import CompanionRef, SourceRef, Workspace, find_workspace_root  # noqa: F401

__all__ = [
    "Workspace", "WorkspacePaths", "SourceRef", "CompanionRef", "find_workspace_root",
    "Registry", "skill_root", "templates_dir",
    "CfError", "WorkspaceError", "SourceError", "DiscoveryError", "StateError",
    "ItemGrammarError", "AdapterError", "GateError",
    "discovery", "gitsafe", "hashing", "inference", "items", "jsonio", "log",
    "paths", "registry", "state", "templates", "vocab", "yamlio",
    "__version__",
]
