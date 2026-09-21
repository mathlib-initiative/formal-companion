"""Proxy / development-order detection (pipeline stage 8).

Two tiers, both mechanical:

* **cheap** (:mod:`detect.def_coverage`) — needs only ``statements.jsonl`` and the
  book's ``items.json``. Classifies every book Definition item as *stated*
  natively, *bridge_only* (only theorems cite it) or *missing*, and flags
  definition bodies that are Mathlib's object wearing a book name.
* **lean** (:mod:`detect.analyze_stages`) — needs a ``deps.json`` produced by
  ``DumpDeps.lean`` inside the companion's built Lean environment, and applies
  the two development-order rules ORDER_VIOLATION and SAME_STAGE_PRIMITIVE.

Ported from the v1 campaign's proxy detector -- its README, ``analyze2.py`` and
``DumpDeps.lean``, none of which are in this repo -- and from that campaign's
def-coverage and uncovered-items cells.  ``docs/design/stage_map.md`` §4 stage 8
is the spec both tiers answer to.
"""
