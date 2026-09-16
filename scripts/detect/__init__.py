"""Proxy / development-order detection (pipeline stage 8).

Two tiers, both mechanical:

* **cheap** (:mod:`detect.def_coverage`) — needs only ``statements.jsonl`` and the
  book's ``items.json``. Classifies every book Definition item as *stated*
  natively, *bridge_only* (only theorems cite it) or *missing*, and flags
  definition bodies that are Mathlib's object wearing a book name.
* **lean** (:mod:`detect.analyze_stages`) — needs a ``deps.json`` produced by
  ``DumpDeps.lean`` inside the companion's built Lean environment, and applies
  the two development-order rules ORDER_VIOLATION and SAME_STAGE_PRIMITIVE.

Ported from /Users/jack/Desktop/tao-differential/adam-detector/ (README.md,
analyze2.py, DumpDeps.lean) and from the def-coverage cells of transcript
b174aa13 (L6099 def-coverage, L6145 uncovered).
"""
