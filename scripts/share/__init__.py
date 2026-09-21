"""Stage 14 -- share a campaign without sharing the book.

New code (docs/design/archive/multi-source-model.md S6.2 is the layout it reads; there is
no recovered original -- the baseline campaign was one directory on one laptop).

The workspace directory is deliberately a git repo with NO remote, because
``corpus/`` quotes 1-3 sentences of a copyrighted source per finding and
``companions/*/statements.jsonl`` holds a private repo's Lean statements.  That
rule was being paid for with the collaboration property: two people could not
work one campaign, a finding could not be reviewed by anyone who had not
personally run the pipeline, and a lost laptop was a lost calibration baseline.

This package is the answer, and it is an EXPORT, not a relaxation of the rule:

* :mod:`share.redact` replaces a book quote with ``{page, length, sha256 of the
  normalised quote, first/last words}`` -- enough to recognise, cite and verify a
  quote, not enough to reconstruct the book;
* :mod:`share.bundle` writes the ledger rows, the routed findings, the tallies,
  the run receipts and source METADATA into a bundle with a ``MANIFEST.json``
  that names every file, its sha256 and the redaction mode;
* :mod:`share.merge` takes such a bundle into another workspace, joining ledger
  rows by finding id and defect key with a conflict rule that never overwrites a
  verdict in silence;
* :mod:`share.hydrate` puts the text back for a holder of the same source, out
  of their OWN ``sources/<id>/text.txt``, by matching the recorded hash.

Nothing here sends anything, and nothing here reads a companion repository.
"""
