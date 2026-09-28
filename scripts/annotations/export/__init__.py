"""Exporters: a companion's own attribution data as an annotation document.

New code, implementing docs/annotations.md section 10 (how the two worked
companions map into the format) as repository commands; the design is
docs/proposals/pdf-to-companion.md section 5.4 (milestone M3).  One module per
companion convention, named like the adapter that reads the same convention:

* ``attribution_table``: a PDF-overlay companion (geometry batches, a
  target-refinement table, a ledger, snippets, destinations, verdicts);
* ``docstring_tags``: a docstring-tagged companion plus its literate site's
  cross-reference index and page manifest.

Each exporter reads the checkout read-only, at a pinned commit, and returns the
document plus a report of what did not fit the format (``common.Report``).
`cf export <convention>` writes both.
"""
