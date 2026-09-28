"""The cost reader, the price table and `cf cost` / `cf harvest --transcripts`.

Milestone M0a of docs/proposals/pdf-to-companion.md (section 5.1) asks the
tool to reproduce a recorded total within 1% from retained usage, to price one
Codex session against a hand computation, and never to price a model the table
does not list.  The fixtures are ``tests/data/m0a/`` (their README says what
each is); every other log here is synthetic and built in a temporary
directory.  ``_hermetic`` is imported first because the harvest test builds a
workspace.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                                  # noqa: E402
from cflib.errors import CfError                                      # noqa: E402
from cost import fleet as fleet_mod                                   # noqa: E402
from cost import pricing                                              # noqa: E402
from cost import usage as usage_mod                                   # noqa: E402

FIXTURES = HERE / "data" / "m0a"
S17B = FIXTURES / "s17b-usage.jsonl.gz"
CODEX_SAMPLE = FIXTURES / "codex-usage-sample.json"

#: the total tests/data/m0a/README.md records for the s17b rows
RECORDED_S17B_USD = 358.10
#: what the tool computes for them at the shipped prices (rounded to 6 places
#: by ``pricing.price``); the assertion below is the 1% band, this is the figure
EXACT_S17B_USD = 358.099941

#: a synthetic price table for the Codex check, in US dollars per million tokens
TEST_TABLE = {
    "gpt-test": {"input": 2.0, "output": 8.0, "cache_write": 1.0, "cache_read": 0.5,
                 "source": "a synthetic table for this test"},
}


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-cost-", dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def claude_line(message_id: str, request_id: str, usage: dict | None, *, model="claude-opus-5",
                kind="assistant") -> dict:
    """One Claude Code transcript line; ``usage=None`` leaves the usage out."""
    message = {"id": message_id, "model": model, "role": "assistant", "content": []}
    if usage is not None:
        message["usage"] = usage
    return {"type": kind, "requestId": request_id, "message": message}


def turn_context(model: str) -> dict:
    return {"type": "turn_context", "payload": {"model": model, "cwd": "<str>"}}


def codex_sample_events() -> list[dict]:
    return json.loads(CODEX_SAMPLE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# the recorded total
# ---------------------------------------------------------------------------

class TestTheRecordedTotalIsReproduced(unittest.TestCase):
    """5,207 exported rows of the section 1.7 re-run price to $358.10."""

    def test_the_s17b_rows_price_to_the_recorded_total_within_one_percent(self):
        totals = usage_mod.read_path(S17B)
        self.assertEqual(totals.message_count, 5207)
        self.assertEqual(totals.transcript_count, 1)
        self.assertEqual(totals.models, ["claude-opus-5"])
        priced = pricing.price(totals.tokens, pricing.DEFAULT_PRICE_TABLE)
        self.assertLessEqual(abs(priced["usd"] - RECORDED_S17B_USD), RECORDED_S17B_USD * 0.01)
        self.assertAlmostEqual(priced["usd"], EXACT_S17B_USD, places=5)
        self.assertEqual(priced["unpriced"], [])
        self.assertEqual(priced["priced"], ["claude-opus-5"])

    def test_the_rows_are_summed_as_written_because_they_carry_no_ids(self):
        """The export repeats identical consecutive rows (the transcripts write
        one line per content block); with no message id to de-duplicate on, a
        usage export is summed as written, and that is what the recorded total
        was computed from."""
        with gzip.open(S17B, "rt", encoding="utf-8") as fh:
            first, second = fh.readline(), fh.readline()
        self.assertEqual(first, second)
        totals = usage_mod.read_path(S17B)
        self.assertEqual(totals.duplicate_lines, 0)

    def test_the_default_table_is_the_issue_11_prices_and_nothing_else(self):
        self.assertEqual(sorted(pricing.DEFAULT_PRICE_TABLE), ["claude-opus-5"])
        opus = pricing.DEFAULT_PRICE_TABLE["claude-opus-5"]
        self.assertEqual((opus["input"], opus["output"], opus["cache_write"], opus["cache_read"]),
                         (5.0, 25.0, 6.25, 0.5))
        self.assertIn("issue #11", opus["source"])
        self.assertEqual(pricing.validate_table(pricing.DEFAULT_PRICE_TABLE), [])


# ---------------------------------------------------------------------------
# Codex rollout logs
# ---------------------------------------------------------------------------

class TestCodexRollouts(unittest.TestCase):
    """The sample's two responses, priced against a hand computation."""

    def setUp(self):
        self.root = scratch(self)

    def rollout(self, events, name="rollout-2026-09-27T00-00-00-sample.jsonl") -> Path:
        return write_jsonl(self.root / name, events)

    def test_the_sample_prices_against_a_hand_computation(self):
        # The sample: response 1 has input 22942 of which 7936 cached, output
        # 373; response 2 has input 23391 of which 22784 cached, output 435.
        # Each is reported twice (token_usage_record, then event_msg token_count).
        #   uncached input  (22942 - 7936) + (23391 - 22784) = 15006 + 607 = 15613
        #   cache read      7936 + 22784                     = 30720
        #   cache write     0
        #   output          373 + 435                        = 808
        # at $2 / $8 / $1 / $0.50 per million:
        #   15613 * 2 + 808 * 8 + 0 * 1 + 30720 * 0.5 = 31226 + 6464 + 15360 = 53050
        #   = $0.05305
        self.rollout([turn_context("gpt-test")] + codex_sample_events())
        totals = usage_mod.read_path(self.root)
        self.assertEqual(totals.files, {"claude_transcript": 0, "codex_rollout": 1,
                                        "usage_export": 0})
        self.assertEqual(totals.tokens, {"gpt-test": {"input": 15613, "output": 808,
                                                      "cache_write": 0, "cache_read": 30720}})
        self.assertEqual(totals.message_count, 2)
        self.assertEqual(totals.repeated_events, 2)
        priced = pricing.price(totals.tokens, TEST_TABLE)
        self.assertAlmostEqual(priced["usd"], 0.05305, places=6)
        self.assertEqual(priced["usd_by_model"], {"gpt-test": 0.05305})
        self.assertEqual(priced["unpriced"], [])

    def test_the_model_comes_from_the_nearest_turn_context(self):
        events = codex_sample_events()
        self.rollout([turn_context("gpt-first")] + events[:2] + [turn_context("gpt-second")]
                     + events[2:])
        totals = usage_mod.read_path(self.root)
        self.assertEqual(totals.tokens["gpt-first"]["output"], 373)
        self.assertEqual(totals.tokens["gpt-second"]["output"], 435)

    def test_a_log_naming_no_model_is_unknown_unless_told(self):
        self.rollout(codex_sample_events())
        totals = usage_mod.read_path(self.root)
        self.assertEqual(totals.models, [usage_mod.UNKNOWN_MODEL])
        self.assertEqual(totals.unknown_model_messages, 2)
        priced = pricing.price(totals.tokens, TEST_TABLE)
        self.assertEqual(priced["usd"], 0.0)
        self.assertEqual(priced["unpriced"], [usage_mod.UNKNOWN_MODEL])
        told = usage_mod.read_path(self.root, model="gpt-test")
        self.assertEqual(told.models, ["gpt-test"])

    def test_a_repeat_with_no_cumulative_block_is_counted_once(self):
        usage = {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 10,
                 "output_tokens": 7, "reasoning_output_tokens": 3, "total_tokens": 107}
        record = {"type": "token_usage_record", "payload": {"thread_id": "t", "usage": usage}}
        other = {"type": "token_usage_record",
                 "payload": {"thread_id": "t", "usage": dict(usage, input_tokens=101,
                                                             total_tokens=108)}}
        self.rollout([turn_context("gpt-test"), record, record, other])
        totals = usage_mod.read_path(self.root)
        self.assertEqual(totals.message_count, 2)
        self.assertEqual(totals.repeated_events, 1)
        # input 100 - 40 cached - 10 written = 50, then 51
        self.assertEqual(totals.tokens["gpt-test"],
                         {"input": 101, "output": 14, "cache_write": 20, "cache_read": 80})

    def test_a_cumulative_block_that_goes_backwards_restarts_the_baseline(self):
        events = codex_sample_events()
        first = events[0]
        self.rollout([turn_context("gpt-test"), first, first])
        totals = usage_mod.read_path(self.root)
        # the second copy advances the cumulative block by nothing: a repeat
        self.assertEqual(totals.message_count, 1)
        self.assertEqual(totals.repeated_events, 1)


# ---------------------------------------------------------------------------
# Claude Code transcripts
# ---------------------------------------------------------------------------

class TestClaudeTranscripts(unittest.TestCase):
    USAGE = {"input_tokens": 2, "output_tokens": 337, "cache_creation_input_tokens": 8768,
             "cache_read_input_tokens": 18795}

    def setUp(self):
        self.root = scratch(self)

    def test_a_message_without_usage_is_skipped_and_counted(self):
        write_jsonl(self.root / "a.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "x"}},
            claude_line("m1", "r1", self.USAGE),
            claude_line("m2", "r2", None),
            {"type": "assistant", "message": "not an object"},
            claude_line("m3", "r3", {"input_tokens": "two"}),
        ])
        totals = usage_mod.read_path(self.root)
        self.assertEqual(totals.message_count, 1)
        self.assertEqual(totals.skipped_without_usage, 3)
        self.assertEqual(totals.tokens["claude-opus-5"]["output"], 337)

    def test_the_lines_of_one_response_count_once(self):
        """Claude Code writes one line per content block, all with the same
        message id, request id and usage."""
        write_jsonl(self.root / "a.jsonl", [
            claude_line("m1", "r1", self.USAGE), claude_line("m1", "r1", self.USAGE),
            claude_line("m1", "r1", self.USAGE), claude_line("m2", "r2", self.USAGE),
        ])
        totals = usage_mod.read_path(self.root)
        self.assertEqual(totals.message_count, 2)
        self.assertEqual(totals.duplicate_lines, 2)
        self.assertEqual(totals.tokens["claude-opus-5"]["output"], 674)

    def test_an_empty_directory_gives_zero(self):
        totals = usage_mod.read_path(self.root)
        self.assertEqual(totals.tokens, {})
        self.assertEqual((totals.transcript_count, totals.message_count), (0, 0))
        priced = pricing.price(totals.tokens, pricing.DEFAULT_PRICE_TABLE)
        self.assertEqual((priced["usd"], priced["priced"], priced["unpriced"]), (0.0, [], []))
        block = fleet_mod.cost_block(self.root, table=pricing.DEFAULT_PRICE_TABLE,
                                     table_source="test")
        self.assertEqual((block["usd"], block["transcript_count"], block["message_count"]),
                         (0.0, 0, 0))

    def test_a_missing_path_is_refused(self):
        with self.assertRaises(CfError):
            fleet_mod.cost_block(self.root / "nope", table=pricing.DEFAULT_PRICE_TABLE,
                                 table_source="test")

    def test_an_unreadable_line_is_counted_not_charged(self):
        (self.root / "a.jsonl").write_text(
            json.dumps(claude_line("m1", "r1", self.USAGE)) + "\n{not json\n[1, 2]\n",
            encoding="utf-8")
        totals = usage_mod.read_path(self.root)
        self.assertEqual(totals.message_count, 1)
        self.assertEqual(totals.unreadable_lines, 2)

    def test_export_writes_one_row_per_response_and_reads_back_the_same(self):
        write_jsonl(self.root / "logs" / "a.jsonl", [
            claude_line("m1", "r1", self.USAGE), claude_line("m1", "r1", self.USAGE),
            claude_line("m2", "r2", self.USAGE, model="claude-sonnet-5"),
        ])
        out = self.root / "usage.jsonl"
        written = usage_mod.export_rows(self.root / "logs", out)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(rows[0]), ["model", "usage"])
        self.assertEqual(sorted(rows[0]["usage"]), sorted(usage_mod.FIELDS))
        again = usage_mod.read_path(out)
        self.assertEqual(again.tokens, written.tokens)
        self.assertEqual(again.files["usage_export"], 1)


# ---------------------------------------------------------------------------
# pricing
# ---------------------------------------------------------------------------

class TestPricing(unittest.TestCase):
    def test_an_unpriced_model_is_reported_and_never_priced(self):
        tokens = {"claude-opus-5": {"input": 1_000_000, "output": 0, "cache_write": 0,
                                    "cache_read": 0},
                  "some-other-model": {"input": 1_000_000, "output": 1_000_000,
                                       "cache_write": 0, "cache_read": 0}}
        priced = pricing.price(tokens, pricing.DEFAULT_PRICE_TABLE)
        self.assertEqual(priced["usd"], 5.0)
        self.assertEqual(priced["usd_by_model"], {"claude-opus-5": 5.0})
        self.assertEqual(priced["priced"], ["claude-opus-5"])
        self.assertEqual(priced["unpriced"], ["some-other-model"])
        self.assertEqual(priced["unpriced_tokens"], {"some-other-model": tokens["some-other-model"]})
        self.assertNotIn("some-other-model", priced["usd_by_model"])

    def test_the_table_is_validated(self):
        good = {"m": {"input": 1, "output": 2, "cache_write": 3, "cache_read": 4, "source": "s"}}
        self.assertEqual(pricing.validate_table(good), [])
        for bad, needle in (
            ({"m": {"input": 1, "output": 2, "cache_write": 3, "source": "s"}}, "cache_read"),
            ({"m": {"input": -1, "output": 2, "cache_write": 3, "cache_read": 4, "source": "s"}},
             "non-negative"),
            ({"m": {"input": 1, "output": 2, "cache_write": 3, "cache_read": 4}}, "source"),
            ({"m": {"input": 1, "output": 2, "cache_write": 3, "cache_reads": 4, "source": "s"}},
             "unknown key"),
            ({"m": "cheap"}, "must be a map"),
            ({}, "empty"),
            ([], "keyed by model id"),
        ):
            with self.subTest(table=bad):
                problems = pricing.validate_table(bad)
                self.assertTrue(problems)
                self.assertTrue(any(needle in p for p in problems), problems)
        with self.assertRaises(ValueError):
            pricing.price({"m": {"input": 1, "output": 1, "cache_write": 0, "cache_read": 0}},
                          {"m": {"input": 1}})

    def test_the_table_sha_changes_with_a_price(self):
        table = {"m": {"input": 1, "output": 2, "cache_write": 3, "cache_read": 4, "source": "s"}}
        changed = {"m": dict(table["m"], output=3)}
        self.assertNotEqual(pricing.table_sha256(table), pricing.table_sha256(changed))
        self.assertEqual(len(pricing.table_sha256(table)), 64)

    def test_a_workspace_without_the_key_falls_back_to_the_default_and_says_so(self):
        table, source = pricing.from_workspace_data({"version": 2, "budget": {"max_cost_usd": 5}})
        self.assertEqual(table, pricing.DEFAULT_PRICE_TABLE)
        self.assertEqual(source, pricing.SOURCE_DEFAULT)
        table, source = pricing.from_workspace_data({"budget": {"prices": TEST_TABLE}})
        self.assertEqual(table, TEST_TABLE)
        self.assertEqual(source, pricing.SOURCE_WORKSPACE)
        with self.assertRaises(ValueError):
            pricing.from_workspace_data({"budget": {"prices": {"m": {"input": 1}}}})

    def test_the_shipped_template_carries_the_default_table(self):
        from cflib import templates, yamlio

        text = templates.render_template("workspace.yaml.example", {
            "workspace_name": "t", "created": '"2026-01-01"'})
        data = yamlio.load_yaml_minimal(text)
        self.assertEqual(data["budget"]["prices"], pricing.DEFAULT_PRICE_TABLE)
        table, source = pricing.from_workspace_data(data)
        self.assertEqual(source, pricing.SOURCE_WORKSPACE)


# ---------------------------------------------------------------------------
# the commands
# ---------------------------------------------------------------------------

def _run(argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cf_main.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class TestCfCost(unittest.TestCase):
    def setUp(self):
        self.root = scratch(self)

    def test_it_prices_the_fixture_with_the_default_table_outside_a_workspace(self):
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        rc, out, err = _run(["cost", str(S17B)])
        self.assertEqual(rc, 0, err)
        self.assertIn("$358.10", out)
        self.assertIn("claude-opus-5", out)
        self.assertIn("shipped default", out)

    def test_json_is_the_receipt_block(self):
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        rc, out, _ = _run(["cost", str(S17B), "--json"])
        self.assertEqual(rc, 0)
        block = json.loads(out)
        for key in ("tokens", "usd", "priced", "unpriced", "price_table_sha256",
                    "transcript_count", "message_count"):
            self.assertIn(key, block)
        self.assertAlmostEqual(block["usd"], EXACT_S17B_USD, places=5)

    def test_a_prices_file_and_an_unpriced_model_are_both_said_out_loud(self):
        table = self.root / "prices.json"
        table.write_text(json.dumps(TEST_TABLE), encoding="utf-8")
        rc, out, err = _run(["cost", str(S17B), "--prices", str(table)])
        self.assertEqual(rc, 0)
        self.assertIn("UNPRICED", out)
        self.assertIn("claude-opus-5", err)
        self.assertIn("$0.00", out)

    def test_export_writes_rows_only(self):
        write_jsonl(self.root / "logs" / "a.jsonl", [
            claude_line("m1", "r1", TestClaudeTranscripts.USAGE),
            claude_line("m1", "r1", TestClaudeTranscripts.USAGE),
        ])
        out_file = self.root / "usage.jsonl"
        rc, out, _ = _run(["cost", str(self.root / "logs"), "--export", str(out_file)])
        self.assertEqual(rc, 0)
        self.assertIn("wrote 1 usage row(s)", out)
        self.assertEqual(json.loads(out_file.read_text(encoding="utf-8")),
                         {"model": "claude-opus-5",
                          "usage": {"input": 2, "output": 337, "cache_write": 8768,
                                    "cache_read": 18795}})


class TestHarvestWritesTheCostBlock(unittest.TestCase):
    """`cf harvest --transcripts` prices the run into RECEIPT.json; without the
    flag the receipt is what it was, plus ``cost: null``."""

    def setUp(self):
        self.root = scratch(self) / "ws"
        rc, _, err = _run(["init", str(self.root), "--no-git", "-q"])
        self.assertEqual(rc, 0, err)
        self.source = self.root.parent / "findings"
        self.source.mkdir()
        (self.source / "exercise-1-1-1.json").write_text(json.dumps({
            "item": "Exercise 1.1.1", "book_located": True, "book_passage_quote": "q",
            "matches": [], "disagreements": [], "coverage_notes": ""}), encoding="utf-8")

    def harvest(self, *extra) -> tuple[int, str, str]:
        return _run(["--workspace", str(self.root), "harvest", "run-a", "--from",
                     str(self.source), "--no-gate", *extra])

    def receipt(self) -> dict:
        return json.loads((self.root / "corpus" / "run-a" / "RECEIPT.json").read_text(
            encoding="utf-8"))

    def test_the_receipt_carries_the_priced_block_and_the_tally_prints_it(self):
        rc, out, err = self.harvest("--transcripts", str(S17B))
        self.assertEqual(rc, 0, err)
        cost = self.receipt()["cost"]
        self.assertAlmostEqual(cost["usd"], EXACT_S17B_USD, places=5)
        self.assertEqual(cost["priced"], ["claude-opus-5"])
        self.assertEqual(cost["unpriced"], [])
        self.assertEqual(cost["price_table_source"], pricing.SOURCE_WORKSPACE)
        self.assertEqual(cost["price_table_sha256"],
                         pricing.table_sha256(pricing.DEFAULT_PRICE_TABLE))
        self.assertEqual((cost["transcript_count"], cost["message_count"]), (1, 5207))
        self.assertEqual(cost["tokens"]["claude-opus-5"]["cache_read"], 299747343)
        self.assertIn("cost: $358.10", out)
        self.assertTrue(self.receipt()["complete"])
        tally = json.loads((self.root / "reports" / "tally.run-a.json").read_text(
            encoding="utf-8"))
        entry = tally["cost"]["run-a"]
        self.assertAlmostEqual(entry["usd"], EXACT_S17B_USD, places=5)
        self.assertEqual(entry["tasks"], 1)
        self.assertIn("finding files", entry["tasks_from"])
        rc, out, _ = _run(["--workspace", str(self.root), "tally"])
        self.assertIn("cost run-a: $358.10", out)
        self.assertIn("a task over 1 tasks", out)

    def test_without_transcripts_nothing_changes_but_the_null(self):
        rc, out, err = self.harvest()
        self.assertEqual(rc, 0, err)
        receipt = self.receipt()
        self.assertIsNone(receipt["cost"])
        self.assertTrue(receipt["complete"])
        self.assertNotIn("cost:", out)
        rc, out, _ = _run(["--workspace", str(self.root), "tally"])
        self.assertIn("cost not recorded for run(s) run-a", out)

    def test_an_unpriced_model_is_warned_about_and_recorded(self):
        logs = self.root.parent / "logs"
        write_jsonl(logs / "a.jsonl", [
            claude_line("m1", "r1", TestClaudeTranscripts.USAGE, model="some-other-model")])
        rc, out, err = self.harvest("--transcripts", str(logs))
        self.assertEqual(rc, 0, err)
        cost = self.receipt()["cost"]
        self.assertEqual(cost["unpriced"], ["some-other-model"])
        self.assertEqual(cost["usd"], 0.0)
        self.assertIn("UNPRICED", err)
        self.assertIn("some-other-model", err)

    def test_the_task_count_comes_from_the_run_yaml_copy_when_there_is_one(self):
        run_dir = self.root / "runs" / "run-a"
        run_dir.mkdir(parents=True)
        (run_dir / "run.yaml").write_text("run_id: run-a\nmode: attack:vac\ntasks:\n  count: 47\n",
                                          encoding="utf-8")
        rc, _, err = self.harvest("--transcripts", str(S17B))
        self.assertEqual(rc, 0, err)
        entry = fleet_mod.run_cost("run-a", self.root / "corpus" / "run-a")
        self.assertEqual((entry["tasks"], entry["tasks_from"]), (47, "run.yaml tasks.count"))
        self.assertAlmostEqual(entry["usd_per_task"], EXACT_S17B_USD / 47, places=3)

    def test_the_report_runs_table_carries_the_two_cost_columns(self):
        rc, _, err = self.harvest("--transcripts", str(S17B))
        self.assertEqual(rc, 0, err)
        rc, out, err = _run(["--workspace", str(self.root), "report", "--no-verify-args"])
        self.assertEqual(rc, 0, err)
        handoff = sorted((self.root / "reports").glob("handoff.*.md"))[-1].read_text(
            encoding="utf-8")
        header = [l for l in handoff.splitlines() if l.startswith("| run |")][0]
        self.assertIn("cost (USD)", header)
        self.assertIn("USD per task", header)
        row = [l for l in handoff.splitlines() if l.startswith("| `run-a`")][0]
        self.assertIn("| 358.10 | 358.10 |", row)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
