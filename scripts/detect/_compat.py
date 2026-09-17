"""Adapters between :mod:`cflib` and the detect stage.

New code (no original). Everything here is either a re-export of the shared
library -- so a detect module imports one name instead of remembering which
cflib module holds it -- or a small adapter over an API whose shape does not
suit this stage:

* :func:`parse_item` turns ``cflib.items.try_parse`` (which returns an
  ``(Item, Reject)`` pair and includes In-text items) into the "numbered item
  or nothing" that a *book stage* needs;
* :func:`sort_key` is the tolerant form of ``cflib.items.sort_key``, which
  raises on junk -- a detector reports junk, it does not die on it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Sequence

# Allow `python3 scripts/detect/analyze_stages.py` as well as `cf detect-proxies`.
_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - import side effect
    sys.path.insert(0, str(_SCRIPTS))

from cflib import items as _items  # noqa: E402
from cflib import vocab  # noqa: E402,F401  (re-exported: the one vocabulary)
from cflib.errors import CfError  # noqa: E402,F401
from cflib.hashing import finding_id, sha256_file, sha256_text  # noqa: E402,F401
from cflib.jsonio import read_json, read_jsonl, write_json, write_jsonl  # noqa: E402,F401
from cflib.log import info, ok, warn  # noqa: E402,F401

#: item kinds a source uses unless workspace.yaml overrides them for it
DEFAULT_KINDS: tuple[str, ...] = _items.DEFAULT_KINDS


class ItemId:
    """A *numbered* book item: ``Definition 1.2.2``, sub-part ``"(iv)"`` or None.

    The detect stage only ever asks a book item for its stage (the section
    number), so In-text and chapter-level items -- which ``cflib.items.Item``
    also represents -- are filtered out at the boundary rather than checked for
    at every use.
    """

    __slots__ = ("kind", "chapter", "section", "number", "sub", "raw")

    def __init__(self, kind: str, chapter: int, section: int, number: int, sub: str | None, raw: str):
        self.kind = kind
        self.chapter = chapter
        self.section = section
        self.number = number
        self.sub = sub
        self.raw = raw

    @property
    def base(self) -> str:
        """The id without any sub-part: ``Definition 1.2.2``."""
        return f"{self.kind} {self.chapter}.{self.section}.{self.number}"

    @property
    def canonical(self) -> str:
        """``base`` plus the sub-part when the row named one: ``Exercise 1.1.6(i)``."""
        return f"{self.base}{self.sub}" if self.sub else self.base

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return self.canonical

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ItemId({self.canonical!r})"


def parse_item(raw: str, kinds: Sequence[str] | Iterable[str] = DEFAULT_KINDS) -> ItemId | None:
    """Parse a numbered book item id; ``None`` when the grammar refuses it.

    Whitespace-tolerant through ``cflib.items.clean`` (the baseline lost
    ``Exercise    1.3.19`` to a single-space regex) and title-tolerant
    (``Definition 1.2.2 (Lebesgue measurability)`` is ``Definition 1.2.2``).
    """
    if not raw:
        return None
    try:
        parsed, _reject = _items.try_parse(str(raw), tuple(kinds))
    except Exception:
        return None
    if parsed is None or parsed.chapter is None or parsed.section is None or parsed.number is None:
        return None
    return ItemId(
        kind=str(parsed.kind),
        chapter=int(parsed.chapter),
        section=int(parsed.section),
        number=int(parsed.number),
        sub=(parsed.sub or None),
        raw=str(raw),
    )


def item_base(raw: str, kinds: Sequence[str] | Iterable[str] = DEFAULT_KINDS) -> str | None:
    """``"Definition 1.2.2 (Lebesgue measurability)"`` -> ``"Definition 1.2.2"``."""
    parsed = parse_item(raw, kinds)
    return parsed.base if parsed else None


def sort_key(item: str, kinds: Sequence[str] | Iterable[str] = DEFAULT_KINDS) -> tuple:
    """Book order for a list of item ids; unparseable ids sort last, by text."""
    parsed = parse_item(item, kinds)
    if parsed is None:
        return (1, "", 0, 0, 0, str(item))
    return (0, parsed.kind, parsed.chapter, parsed.section, parsed.number, parsed.sub or "")
