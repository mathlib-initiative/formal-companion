"""`cf tally --locate` prints what each run it read cost (smoke 2: receipt pricing).

New code, for docs/proposals/wikipedia-locate.md sections 6 and 7 (cost and
spend) and references/locate-documents.md section 10.  The second smoke's
receipts all had ``cost: null``: nothing priced a locate run without the agents'
transcripts.  ``cf harvest`` now prices a remote locate run from the bridge's
kept envelopes (tests/test_harvest_recursive.py: ``TestEnvelopesPriceTheRun``);
this pins the other half, that the locate tally prints the cost block, its
basis and its envelope count, for every run it read, and records it in the
tally report.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for _path in (SCRIPTS, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from cflib.jsonio import read_json  # noqa: E402
from cost import fleet, pricing  # noqa: E402

from test_locate_planner import Campaign, ws  # noqa: E402


class TestTheLocateTallyPrintsTheCost(Campaign):

    def setUp(self):
        super().setUp()
        self.locate()
        envelopes = self.root.parent / "envelopes"
        envelopes.mkdir()
        for name, usd in (("t-1.worker.a.1.json", 3.25), ("t-1.reviewer.b.2.json", 0.75)):
            (envelopes / name).write_text(json.dumps({
                "total_cost_usd": usd, "duration_ms": 1000,
                "modelUsage": {"claude-opus-5": {"inputTokens": 10, "outputTokens": 20,
                                                 "cacheCreationInputTokens": 0,
                                                 "cacheReadInputTokens": 100,
                                                 "costUSD": usd}}}))
        block = fleet.envelope_cost_block(envelopes, table=pricing.DEFAULT_PRICE_TABLE,
                                          table_source=pricing.SOURCE_DEFAULT)
        receipt_path = self.paths.corpus_run_dir("loc-1") / "RECEIPT.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["cost"] = block
        receipt_path.write_text(json.dumps(receipt))

    def test_the_cost_line_names_its_source_and_envelopes(self):
        code, out, err = ws(self.root, "tally", "--locate", "--run", "loc-1")
        self.assertEqual(code, 0, out + err)
        text = out + err
        self.assertIn("cost loc-1: $4.00", text)
        self.assertIn("source: envelopes, 2 of them", text)
        # the inventory run it read has no cost block, and is named
        self.assertIn("cost not recorded for run(s) inv-1", text)
        report = read_json(self.paths.locate_tally_json("loc-1"))
        self.assertEqual(report["cost"]["loc-1"]["usd"], 4.0)
        self.assertEqual(report["cost"]["loc-1"]["basis"], "envelopes")
        self.assertIsNone(report["cost"]["inv-1"])

    def test_the_json_carries_it_too(self):
        code, out, err = ws(self.root, "tally", "--locate", "--run", "loc-1", "--json")
        self.assertEqual(code, 0, out + err)
        doc = json.loads(out[out.index("{"):])
        self.assertEqual(doc["cost"]["loc-1"]["envelope_count"], 2)


if __name__ == "__main__":
    import unittest

    unittest.main()
