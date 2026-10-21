"""The locate benchmark: gold, draws, splits, metrics and the acceptance rule.

New code, implementing docs/proposals/wikipedia-locate.md sections 8 and 9 in
the file formats references/locate-bench.md defines.  ``cf bench`` (one module,
``scripts/cfcmd/bench.py``) drives it:

* :mod:`bench.draw` -- the frame, the seeded draws with their inclusion
  probabilities, article clusters and the split by cluster hash, the G3 span
  sample (section 8.5);
* :mod:`bench.curated` -- Mathlib's 1000.yaml and 100.yaml and plain QID
  lists, read one way for the frame's curated stratum and for G1 (sections
  8.1 and 8.5);
* :mod:`bench.gold` -- G1 resolution and anchoring, the frozen labeler's
  emit/transcribe (G1 relations, G3 blind labels, the hunter recall *h*, GX
  inventories), G4 spot checks, the planted index and the gold manifest
  (sections 8.1 and 8.4);
* :mod:`bench.metrics` -- the span-overlap join and every section 8.2 metric,
  each kept per article cluster so an interval is a cluster bootstrap;
* :mod:`bench.score` -- the score document, its pins and ``measurement_id``,
  and the refusals (section 8.3);
* :mod:`bench.compare` -- the section 9 acceptance rule and the loop ledger;
* :mod:`bench.carry` -- which gold rows a re-cut carries, re-anchors on a new
  layer or relabels, over the carry-over map of docs/proposals/statement-cut.md
  section 4.

``cf score`` and ``scripts/score/`` are a different measurement (faithfulness
defects against an answer key) and are untouched.
"""
