"""The cost of a fleet, read from the agents' own logs and priced by model id.

New code for milestone M0a of docs/proposals/pdf-to-companion.md (section 5.1),
following the design of issue #11: the orchestrator sees no token, but every
agent session leaves a transcript, and summing the usage those transcripts
carry prices a run exactly at list prices.

`usage.py` reads the logs (Claude Code transcripts, Codex rollout logs, or a
usage export made from either) and sums tokens per model id; it imports nothing
from ``cflib`` so it can run on the host that holds the transcripts.
`pricing.py` holds the price table and prices the totals; a model the table
does not list is reported, never guessed at. `fleet.py` turns the two into the
``cost`` block `cf harvest --transcripts` writes into a run's ``RECEIPT.json``
and the per-task figures `cf tally` and `cf report` print from it.
"""
