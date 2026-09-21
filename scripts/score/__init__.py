"""Scoring a run against an answer key: the repo's recall instrument.

New code (the design critique has no stage for it; references/method.md S10
"What the measured numbers do not license" is the reason it exists).

Every other stage measures what the fleet DID report.  Nothing measured what it
did NOT: a defect no worker raised leaves no trace in the corpus, the ledger or
the tally, so "the companion is faithful" and "nobody looked" produce identical
artifacts.  This package closes that by scoring findings against a fixture whose
defects are known in advance:

``answer_key``  the key file: planted defects, decoys and clean loci;
``rows``        corpus documents -> one row per (source, repo, item, kind);
``score``       the join, the per-kind precision/recall/F1 and the table;
``mutate``      the seeded mutation generator that plants a defect into a
                synthetic companion, so the key describes something a script
                produced rather than something a human remembered to write down.

The join key is run-independent -- ``(source_id, repo, join_key(item), kind)`` --
so a key written once scores every later run, model or mechanical.
"""
