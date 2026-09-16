"""Shared library for the companion-faithfulness campaign CLI.

Import from here, not from sibling stage packages:

    from cflib import items, vocab, state
    from cflib.campaign import Campaign
    from cflib.errors import CfError

Every module is stdlib-only (PyYAML is used when importable, with a fallback
reader in :mod:`cflib.yamlio`).  Nothing here touches the network.
"""
from __future__ import annotations

__version__ = "0.1.0"

from . import hashing, items, jsonio, log, paths, state, templates, vocab, yamlio  # noqa: F401
from .campaign import Campaign, find_campaign_root  # noqa: F401
from .errors import (  # noqa: F401
    AdapterError, CampaignError, CfError, GateError, ItemGrammarError, StateError,
)
from .paths import CampaignPaths, skill_root, templates_dir  # noqa: F401

__all__ = [
    "Campaign", "CampaignPaths", "find_campaign_root", "skill_root", "templates_dir",
    "CfError", "CampaignError", "StateError", "ItemGrammarError", "AdapterError", "GateError",
    "hashing", "items", "jsonio", "log", "paths", "state", "templates", "vocab", "yamlio",
    "__version__",
]
