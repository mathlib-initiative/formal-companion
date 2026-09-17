"""TEMPORARY COMPATIBILITY SHIM -- delete me.

``scripts/book/`` became ``scripts/sources/`` (docs/design/multi-source-model.md
S6.2: a workspace holds M sources, so "the book" is not a thing any more).  The
package's real home is ``scripts/sources``; this module only re-publishes it
under the old import path so that the modules STAGES-A has not migrated yet --
``scripts/pair/compat.py``, ``scripts/cfcmd/pair.py`` and
``tests/test_book_pair.py`` -- still import.

Nothing here restores the old behaviour: ``book.prepare_book`` IS
``sources.prepare_source``, whose ``prepare()`` takes ``source_id`` and writes
``text.txt`` / ``meta.json``.  A caller that still passes ``book_id=`` fails
loudly at that call, which is the point.  What the shim buys is that the
failures STAGES-A has to fix are visible one by one, instead of one ImportError
hiding a whole test module.

WHEN THIS FILE GOES: with the STAGES-A package, which renames those imports and
``tests/test_book_pair.py`` -> ``tests/test_sources_pair.py``.
"""
from __future__ import annotations

import sys

from sources import compat, fetch_errata, render_pages  # noqa: F401

sys.modules[__name__ + ".compat"] = compat
sys.modules[__name__ + ".fetch_errata"] = fetch_errata
sys.modules[__name__ + ".render_pages"] = render_pages

# a REAL file, not an alias: it restores the one module-level constant an alias
# cannot carry (BOOK_KINDS -> SOURCE_KINDS).
from . import prepare_book  # noqa: E402,F401

__all__ = ["compat", "fetch_errata", "prepare_book", "render_pages"]
