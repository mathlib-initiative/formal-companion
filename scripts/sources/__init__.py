"""Stage 1: source preparation (text, page map, items, errata, page images).

One source directory serves every companion bound to that source
(docs/design/multi-source-model.md S6.2), which is the point of the multi-source
model: two companions of the same book prepare the text ONCE and are compared
against the same bytes.

Nothing in this package ever writes source text into the skill repo: the outputs
belong in ``<workspace>/sources/<source_id>/``, which the workspace .gitignore
keeps out of git.

(Was ``scripts/book/``. The skill repo's own .gitignore carries the workspace
patterns, so the ``sources/`` line there needs the ``!scripts/sources/``
negation or this package vanishes from git -- see references/traps.md.)
"""
