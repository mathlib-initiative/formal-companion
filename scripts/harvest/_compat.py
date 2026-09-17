"""The cflib names the harvest/tally stage uses, plus its own two helpers.

New code.  Everything shared lives in ``scripts/cflib``; this module re-exports
what harvest/*.py uses and adds:

* ``SUBSTANTIVE_KINDS`` -- the kinds that mean "the Lean says something
  different from the book", i.e. every finding kind except COVERAGE (a missing
  theorem, not a wrong one) and PAGE_ANCHOR (a citation).  The distinction is
  the whole point of the tally: 22 of the baseline's 40 flagged items were
  pagination alone.  It is read from ``cflib.vocab`` when that grows it.
* ``iter_finding_files`` -- a corpus directory holds findings plus a RECEIPT and
  a run.yaml copy, and counting the receipt as a finding is exactly the kind of
  off-by-one the receipt exists to prevent.

Also fixes sys.path when a module here is executed directly
(``python3 scripts/harvest/tally.py``) rather than through ``scripts/cf.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib import hashing, items as cf_items, log, state as cf_state, vocab  # noqa: E402
from cflib.workspace import Workspace, from_args  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json, read_jsonl, write_json, write_jsonl  # noqa: E402
from cflib.yamlio import read_yaml, write_yaml  # noqa: E402

__all__ = [
    "Workspace", "CfError", "from_args", "hashing", "cf_items", "cf_state", "log", "vocab",
    "read_json", "read_jsonl", "write_json", "write_jsonl", "read_yaml", "write_yaml",
    "SEVERITIES", "SUBSTANTIVE_KINDS", "iso_now", "iter_finding_files", "source_kinds",
]

iso_now = cf_state.now

SEVERITIES = tuple(getattr(vocab, "SEVERITIES", ("low", "medium", "high")))
SUBSTANTIVE_KINDS = frozenset(getattr(
    vocab, "SUBSTANTIVE_KINDS",
    frozenset(vocab.FINDING_KINDS) - {vocab.COVERAGE, vocab.PAGE_ANCHOR},
))

#: files that live beside findings in a corpus directory and are not findings.
#: ``scripts/gate/verify_findings.py`` carries an identical literal (it runs on
#: a server with nothing on the path) -- keep the two in sync, or a re-gate of a
#: harvested corpus fails on the receipt this module wrote.
NON_FINDING_FILES = {"RECEIPT.json", "run.yaml", "run.json", "manifest.json",
                     "tally.json", "journal-harvest.json", "gate.json",
                     "gate.local.json"}


def iter_finding_files(directory: str | Path) -> list[Path]:
    """Sorted *.json findings in *directory*, excluding receipts and manifests."""
    d = Path(directory)
    if not d.is_dir():
        return []
    return [p for p in sorted(d.iterdir())
            if p.suffix == ".json" and p.name not in NON_FINDING_FILES
            and not p.name.startswith(".")]


def source_kinds(workspace, source_id: str | None = None) -> Sequence[str]:
    """One SOURCE's item kinds, from workspace.yaml when it names them.

    The kinds list is per source, not per workspace: a source whose grammar has
    no ``Proposition`` must not have one invented for it by a sibling edition,
    because the kinds decide which labels parse at all.
    """
    kinds = None
    getter = getattr(workspace, "kinds", None)
    if callable(getter):
        try:
            kinds = getter(source_id) if source_id else None
        except Exception:  # noqa: BLE001 - an unknown source falls back to the default
            kinds = None
    elif getter:
        kinds = getter
    return list(kinds) if kinds else list(cf_items.DEFAULT_KINDS)
