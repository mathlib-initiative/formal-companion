"""TEMPORARY COMPATIBILITY SHIM -- delete me.

``book.prepare_book`` IS :mod:`sources.prepare_source` (docs/design/multi-source-model.md
S6.2).  This module exists only so the modules the STAGES-A package has not
migrated yet -- ``tests/test_book_pair.py`` above all -- still import, and so the
failures inside them are the real ones (a missing ``source_id``) rather than one
ImportError that hides sixty tests.

The ONE old name restored here is ``BOOK_KINDS``: a module-level constant cannot
be reached through a module alias, and it is now ``SOURCE_KINDS`` because the
kinds belong to ONE source and are inferred from that source's own headings
(S5.2), not to "the book".

WHEN THIS FILE GOES: with the STAGES-A package, which renames
``tests/test_book_pair.py`` -> ``tests/test_sources_pair.py`` and its imports.
"""
from __future__ import annotations

from sources.prepare_source import *            # noqa: F401,F403
from sources.prepare_source import SOURCE_KINDS  # noqa: F401

#: was `prepare_book.BOOK_KINDS`
BOOK_KINDS = SOURCE_KINDS
