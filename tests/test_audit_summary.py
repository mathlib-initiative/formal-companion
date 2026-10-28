"""The harvest of an audited inventory run: the ``audit`` block and the cost by step.

New code, for references/locate-documents.md section 13.10 (the receipt's ``audit``
block, ``scripts/harvest/audit_summary.py``) and section 10 (the tend root's ``audit/``
fetched beside the gate log, ``harvest.host_artifacts``; the envelope cost block's
``by_step``, ``cost.fleet``).  The audit directory here is synthetic: hand-written turn
states and verdicts files in the shapes section 13 gives, over made-up documents.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for _path in (SCRIPTS, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from cflib import vocab  # noqa: E402
from cost import fleet, pricing  # noqa: E402
from harvest import audit_summary as A  # noqa: E402
from harvest import harvest as harvest_mod  # noqa: E402
from harvest import host_artifacts as host_mod  # noqa: E402

import test_harvest_recursive as THR  # noqa: E402

AUDITED = {"locate": {"mode": "locate-inventory", "audit": {"rule": 1, "review_rounds": 2}}}


def write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verdicts(k: int, doc_sha: str, decision: str, actionable: list[str], *, formal: int = 10,
             kind_other: int = 2, harm: bool = False, audit_failed: bool = False,
             extra: dict | None = None) -> dict:
    counts = {"formal": formal, "actionable": len(actionable),
              "by_verdict": {v: actionable.count(v) for v in sorted(set(actionable))},
              "kind_other_formal": kind_other, "kind_other_in_proof_or_example": 1,
              **(extra or {})}
    return {"schema": vocab.INVENTORY_AUDIT_SCHEMA, "kind": "verdicts", "round": k,
            "review_rounds": 2, "document_sha256": doc_sha, "decision": decision,
            "verdicts": [{"id": f"r{k}-v{i + 1:03d}", "verdict": v}
                         for i, v in enumerate(actionable)],
            "counts": counts, "harm": harm, "audit_failed": audit_failed}


def state(task: str, sid: str, *, approved: tuple[str, str] | None = None,
          best: str | None = None, rejections: int = 0, **counts) -> dict:
    base = {"reviewer_tampered": 0, "gate_resets": 0, "audit_failed": 0, "precheck_refused": 0,
            "reverts": 0, "retries": 0, "unaudited": 0, "repair_invalid": 0}
    base.update(counts)
    return {"schema": vocab.AUDIT_TURN_SCHEMA, "task_id": task, "source_id": sid,
            "approved": ({"round": 1, "sha256": approved[0], "why": approved[1]}
                         if approved else None),
            "best": {"round": 1, "sha256": best, "snapshot": "x"} if best else None,
            "rejections": rejections, "counts": base}


class Fixture:
    """A landed corpus of seven documents and the audit directory a fetch brought home."""

    def __init__(self, case: unittest.TestCase):
        self.root = Path(tempfile.mkdtemp())
        case.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.corpus = self.root / "corpus"
        self.audit = self.root / "host" / "audit"
        self.shas: dict[str, str] = {}
        for letter in "abcdefh":
            text = json.dumps({"schema": "cf-locate/1", "mode": "locate-inventory",
                               "task_id": f"t-{letter}", "source_id": f"s-{letter}",
                               "passages": []})
            path = self.corpus / f"t-{letter}" / f"s-{letter}.json"
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
            self.shas[letter] = sha(text)
        s = self.shas
        # a: rejected in round 1, repaired, approved in round 2
        self.task("a", state("t-a", "s-a", approved=(s["a"], "the final round"), rejections=1),
                  {1: verdicts(1, "old-a", "request_changes",
                               ["statement_misstates", "wrong_kind"],
                               extra={"flagged": 3, "upheld": 2, "refuted": 1}),
                   2: verdicts(2, s["a"], "approve", ["wrong_kind"], kind_other=1,
                               extra={"status_applied": 1, "status_missing": 1})})
        # b: round 2 found harm and reverted to round 1's snapshot, the bytes harvested
        self.task("b", state("t-b", "s-b", approved=(s["b"], "reverted to round 1's snapshot"),
                             rejections=1, reverts=1),
                  {1: verdicts(1, s["b"], "request_changes", ["over_cut"]),
                   2: verdicts(2, "new-b", "revert", ["revert", "over_cut"], harm=True,
                               extra={"collateral": 1, "collateral_harmful": 1})})
        # c: the audit approved other bytes than the ones that landed
        self.task("c", state("t-c", "s-c", approved=("other", "no actionable verdict")),
                  {1: verdicts(1, "other", "approve", [])})
        # d: no audit record at all (t-d is absent from the audit directory)
        # e: an unresolved document, approved unaudited after the precheck
        self.task("e", state("t-e", "s-e", approved=(s["e"], "not audited: an unresolved "
                                                             "document"), unaudited=1), {})
        # f: the audited cap restored the best snapshot
        self.task("f", state("t-f", "s-f", best=s["f"], rejections=2), {
            1: verdicts(1, s["f"], "request_changes", ["under_cut"])})
        # h: an audit whose output stayed invalid, approved as audit_failed
        self.task("h", state("t-h", "s-h", approved=(s["h"], "audit_failed: no output"),
                             audit_failed=1, retries=1),
                  {1: verdicts(1, s["h"], "approve", [], audit_failed=True)})
        # g: a task the audit reviewed that never merged
        self.task("g", state("t-g", "s-g"), {})
        (self.audit / "t-a" / "s-a" / "r9.verdicts.json").write_text("{not json")
        # neither of these is a round's record
        write(self.audit / "t-a" / "s-a" / "discarded" / "r1.verdicts.json", {"kind": "x"})
        write(self.audit / "t-a" / "s-a" / "raw" / "r1.audit.part1.a1.json", {"x": 1})

    def task(self, letter: str, st: dict, rounds: dict) -> None:
        write(self.audit / f"t-{letter}" / A.STATE, st)
        for k, v in rounds.items():
            write(self.audit / f"t-{letter}" / f"s-{letter}" / f"r{k}.verdicts.json", v)

    def summarise(self, envelopes: Path | None = None) -> dict:
        return A.summarise(self.corpus, AUDITED["locate"]["audit"], audit_dir=self.audit,
                           source=A.SOURCE_FETCHED, envelopes=envelopes)


class TestThePlan(unittest.TestCase):

    def test_only_a_run_yaml_naming_the_audit_has_one(self):
        self.assertEqual(A.plan_of(AUDITED), {"rule": 1, "review_rounds": 2})
        self.assertIsNone(A.plan_of({"locate": {"mode": "locate-inventory"}}))
        self.assertIsNone(A.plan_of({}))
        self.assertIsNone(A.plan_of(None))

    def test_every_outcome_is_the_vocabularys(self):
        self.assertEqual(set(vocab.AUDIT_OUTCOMES), {"approved", "reverted", "audit_failed",
                                                     "not_audited", "capped", "stale",
                                                     "unaudited"})
        self.assertEqual(sorted(vocab.AUDIT_COST_STEPS.values()),
                         sorted(["worker", *vocab.AUDIT_STEPS]))
        documented = (HERE.parent / "references" / "vocabulary.md").read_text(encoding="utf-8")
        for name in ("AUDIT_OUTCOMES", "AUDIT_COST_STEPS", *vocab.AUDIT_OUTCOMES,
                     *vocab.AUDIT_COST_STEPS):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", documented)


class TestTheBlock(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture(self)
        self.block = self.fx.summarise()

    def test_each_documents_outcome_is_judged_by_its_bytes(self):
        per = self.block["per_source"]
        self.assertEqual({sid: row["outcome"] for sid, row in per.items()},
                         {"s-a": "approved", "s-b": "reverted", "s-c": "stale",
                          "s-d": "unaudited", "s-e": "not_audited", "s-f": "capped",
                          "s-h": "audit_failed"})
        self.assertEqual(self.block["outcomes"], {
            "approved": 1, "reverted": 1, "audit_failed": 1, "not_audited": 1, "capped": 1,
            "stale": 1, "unaudited": 1})
        self.assertEqual(self.block["stale"], ["s-c"])
        self.assertEqual(self.block["unaudited"], ["s-d"])
        self.assertEqual(self.block["not_harvested"], ["t-g"])
        self.assertEqual(self.block["unreadable"], ["t-a/s-a/r9.verdicts.json"])
        self.assertEqual((self.block["documents"], self.block["audited"]), (7, 5))
        self.assertEqual(self.block["source"], "fetched")

    def test_the_residual_is_the_round_that_reviewed_the_harvested_bytes(self):
        per = self.block["per_source"]
        self.assertEqual((per["s-a"]["reviewed_round"], per["s-a"]["residual"]), (2, 1))
        # the revert harvested round 1's snapshot: round 1's verdicts are its residual
        self.assertEqual((per["s-b"]["reviewed_round"], per["s-b"]["residual"]), (1, 1))
        self.assertEqual(per["s-b"]["final_round"], 2)
        self.assertEqual(per["s-b"]["decision"], "revert")
        self.assertEqual(per["s-a"]["actionable_by_round"], [2, 1])
        self.assertIsNone(per["s-c"]["reviewed_round"])
        res = self.block["residual"]
        # a (1 wrong_kind), b (1 over_cut), f (1 under_cut), h (none)
        self.assertEqual((res["documents"], res["actionable"], res["formal"]), (4, 3, 40))
        self.assertEqual(res["by_verdict"], {"over_cut": 1, "under_cut": 1, "wrong_kind": 1})
        self.assertEqual(res["per_formal"], 0.075)
        # the kind counts over the same documents: a went from 2 to 1, b, f, h stayed at 2
        self.assertEqual((res["kind_other_formal_before"], res["kind_other_formal_after"]),
                         (8, 7))

    def test_the_rounds_sum_the_verdicts_files(self):
        r1, r2 = self.block["rounds"]["1"], self.block["rounds"]["2"]
        self.assertEqual(r1["documents"], 5)                     # a, b, c, f, h
        self.assertEqual(r1["decisions"], {"approve": 2, "request_changes": 3, "revert": 0})
        self.assertEqual((r1["rejected"], r1["audit_failed"]), (3, 1))
        self.assertEqual(r1["counts"]["actionable"], 4)
        self.assertEqual(r1["counts"]["flagged"], 3)
        self.assertEqual(r1["counts"]["by_verdict"], {"over_cut": 1, "statement_misstates": 1,
                                                      "under_cut": 1, "wrong_kind": 1})
        self.assertEqual(r2["decisions"], {"approve": 1, "request_changes": 0, "revert": 1})
        self.assertEqual((r2["harm"], r2["counts"]["collateral_harmful"]), (1, 1))
        self.assertEqual(r2["counts"]["status_applied"], 1)

    def test_the_turns_sum_the_states(self):
        turns = self.block["turns"]
        self.assertEqual(turns["tasks"], 7)                      # g's state too
        self.assertEqual(turns["rejections"], 4)
        self.assertEqual((turns["reverts"], turns["unaudited"], turns["audit_failed"],
                          turns["retries"]), (1, 1, 1, 1))

    def test_no_audit_directory_leaves_every_document_unaudited_and_says_so(self):
        block = A.summarise(self.fx.corpus, {"rule": 1, "review_rounds": 2}, audit_dir=None,
                            source=A.SOURCE_KEPT)
        self.assertIsNone(block["source"])
        self.assertEqual(block["outcomes"]["unaudited"], 7)
        self.assertEqual(block["rounds"], {})
        lines = A.problems(block)
        self.assertTrue(any("no audit directory" in line for line in lines), lines)
        self.assertFalse(any("unaudited" in line for line in lines), lines)

    def test_the_harvest_warns_and_describes(self):
        lines = A.problems(self.block)
        self.assertTrue(any("stale" in line and "s-c" in line for line in lines), lines)
        self.assertTrue(any("unaudited" in line and "s-d" in line for line in lines), lines)
        self.assertTrue(any("could not be read" in line for line in lines), lines)
        text = "\n".join(A.describe(self.block))
        self.assertIn("audit: rule 1, 2 review round(s); 5 of 7 document(s) audited", text)
        self.assertIn("round 1: 5 document(s) (approve 2, request_changes 3)", text)
        self.assertIn("residual: 3 actionable verdict(s) over 40 formal passage(s)", text)


def skip_report(skips: int, math: int, furniture: int, mostly: list[str], **by_section) -> dict:
    return {"skips": skips, "chars": 100 * skips, "math_paragraphs": math,
            "furniture_math_paragraphs": furniture,
            "by_section": {k: {"skipped": a, "math": b} for k, (a, b) in by_section.items()},
            "mostly_skipped": mostly}


class TestTheSkipsBlock(unittest.TestCase):
    """Audit rule 2's ``audit.skips`` (references/locate-documents.md section 13.11): before
    (round 1's skip report) and after (the reviewed round's), by source; none for rule 1."""

    def test_rule_1_verdicts_files_give_no_skips_key(self):
        block = Fixture(self).summarise()
        self.assertNotIn("skips", block)
        self.assertFalse(any("skips:" in line for line in A.describe(block)))

    def test_rule_2_gives_before_after_and_by_section(self):
        fx = Fixture(self)
        s = fx.shas
        first = skip_report(4, 7, 3, ["Proof"], **{"(lead)": (3, 3), "Proof": (4, 4)})
        last = skip_report(3, 3, 3, [], **{"(lead)": (3, 3)})
        for k, report, sha_, decision in ((1, first, "old-a", "request_changes"),
                                          (2, last, s["a"], "approve")):
            v = verdicts(k, sha_, decision, ["skipped_statements"] if k == 1 else [],
                         extra={"skips": report["skips"], "skip_rule": int(k == 1)})
            v.update(schema=vocab.INVENTORY_AUDIT_SCHEMAS[2], decision_rule="audit/2",
                     skip_report=report)
            write(fx.audit / "t-a" / "s-a" / f"r{k}.verdicts.json", v)
        block = fx.summarise()
        skips = block["skips"]
        self.assertEqual(skips["before"], {"skips": 4, "chars": 400, "math_paragraphs": 7,
                                           "furniture_math_paragraphs": 3, "outside": 4,
                                           "mostly_skipped": 1, "documents": 1})
        self.assertEqual((skips["after"]["skips"], skips["after"]["outside"],
                          skips["after"]["mostly_skipped"]), (3, 0, 0))
        self.assertEqual(skips["by_source"], {"s-a": {"before": first, "after": last}})
        self.assertEqual(skips["by_source"]["s-a"]["before"]["by_section"]["Proof"],
                         {"skipped": 4, "math": 4})
        # the round rows sum the skip counts as any count
        self.assertEqual(block["rounds"]["1"]["counts"]["skip_rule"], 1)
        self.assertIn("skips: 4 -> 3, holding 4 -> 0 math paragraph(s) outside the lead and "
                      "back matter; 1 -> 0 mostly skipped section(s)",
                      "\n".join(A.describe(block)))


def _envelopes(directory: Path) -> Path:
    for name, usd in (("t-a.worker.20261022T101010Z.11.json", 4.0),
                      ("t-a.reviewer.audit.20261022T101011Z.12.json", 2.0),
                      ("t-a.reviewer.skeptic.20261022T101012Z.13.json", 0.5),
                      ("t-a.worker.revision.20261022T101013Z.14.json", 1.5),
                      ("t-a.reviewer.check.20261022T101014Z.15.json", 0.75),
                      ("t-a.reviewer.skeptic.20261022T101015Z.16.json", None)):
        write(directory / name, {"total_cost_usd": usd, "duration_ms": 1000,
                                 "modelUsage": {"claude-opus-5": {
                                     "inputTokens": 1, "outputTokens": 2,
                                     "cacheCreationInputTokens": 0,
                                     "cacheReadInputTokens": 3, "costUSD": usd or 0}}})
    return directory


class TestTheCostByStep(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_audit_block_prices_each_step(self):
        fx = Fixture(self)
        block = fx.summarise(_envelopes(self.tmp / "envelopes"))
        self.assertEqual(block["cost_usd"], {"worker_initial": 4.0, "worker_revision": 1.5,
                                             "audit": 2.0, "skeptic": 0.5, "check": 0.75})
        self.assertEqual(block["cost_calls"]["skeptic"], 2)
        self.assertEqual(block["cost_unpriced_calls"], 1)
        self.assertIn("cost by step: worker_initial $4.00", "\n".join(A.describe(block)))
        self.assertIsNone(fx.summarise()["cost_usd"])

    def test_the_envelope_cost_block_splits_by_step_only_when_one_is_named(self):
        envelopes = _envelopes(self.tmp / "envelopes")
        block = fleet.envelope_cost_block(envelopes, table=pricing.DEFAULT_PRICE_TABLE,
                                          table_source=pricing.SOURCE_DEFAULT)
        self.assertEqual(block["by_step"], {
            "audit": {"envelopes": 1, "usd": 2.0, "unpriced_calls": 0},
            "check": {"envelopes": 1, "usd": 0.75, "unpriced_calls": 0},
            "revision": {"envelopes": 1, "usd": 1.5, "unpriced_calls": 0},
            "skeptic": {"envelopes": 2, "usd": 0.5, "unpriced_calls": 1},
            "worker": {"envelopes": 1, "usd": 4.0, "unpriced_calls": 0}})
        self.assertEqual(block["usd"], 8.75)
        self.assertIn("by step: audit $2.00 (1 envelope(s))",
                      "\n".join(fleet.describe(block)))
        self.assertEqual(fleet.envelope_by_step(envelopes), block["by_step"])
        # an unaudited run's envelopes name no step: no key, the block as it was
        plain = self.tmp / "plain"
        plain.mkdir()
        for path in envelopes.iterdir():
            if not fleet.envelope_step(path.name)[1]:
                shutil.copy2(path, plain / path.name)
        write(plain / "t-b.reviewer.20261022T101016Z.17.json", {"total_cost_usd": 1.0})
        write(plain / "odd-name.json", {"total_cost_usd": 1.0})
        unstepped = fleet.envelope_cost_block(plain, table=pricing.DEFAULT_PRICE_TABLE,
                                              table_source=pricing.SOURCE_DEFAULT)
        self.assertNotIn("by_step", unstepped)
        self.assertNotIn("by step", "\n".join(fleet.describe(unstepped)))
        self.assertEqual(fleet.envelope_step("odd-name.json"), (fleet.STEP_UNNAMED, False))
        self.assertEqual(fleet.envelope_by_step(self.tmp / "nowhere"), {})


class TestTheAuditDirectoryComesHome(THR._HostHarvest):
    """A remote harvest of an audited run fetches the tend root's ``audit/`` beside the
    envelopes, and the receipt carries the block; an unaudited run's does neither."""

    def audited(self) -> None:
        run_yaml = self.ws / "runs" / "r-1" / "run.yaml"
        text = run_yaml.read_text(encoding="utf-8")
        run_yaml.write_text(text.replace("locate:\n  mode: locate\n", "locate:\n  mode: locate\n"
                                         "  audit:\n    rule: 1\n    review_rounds: 2\n"),
                            encoding="utf-8")
        doc = self.remote / "r-1" / "findings" / "t-loc-0001" / f"{THR.tvl.SID}.json"
        digest = hashlib.sha256(doc.read_bytes()).hexdigest()
        audit = self.remote / "r-1-tend" / "audit" / "t-loc-0001"
        write(audit / A.STATE, state("t-loc-0001", THR.tvl.SID,
                                     approved=(digest, "no actionable verdict")))
        write(audit / THR.tvl.SID / "r1.verdicts.json",
              verdicts(1, digest, "approve", [], formal=3))
        (audit / THR.tvl.SID / "r1.render.part1.md").write_text("a rendering: not fetched")
        THR._envelope(self.remote / "r-1-tend" / "usage",
                      "t-loc-0001.reviewer.audit.20261022T101011Z.12.json", 2.0)

    def receipt(self) -> dict:
        return json.loads((self.ws / "corpus" / "r-1" / "RECEIPT.json").read_text())

    def test_the_block_from_the_fetched_directory(self):
        self.audited()
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)
        receipt = self.receipt()
        row = receipt["host_artifacts"]["audit"]
        self.assertEqual((row["fetched"], row["expected"], row["files"]), (True, True, 2))
        self.assertEqual(row["remote"], f"{self.remote}/r-1-tend/audit")
        host = self.ws / "runs" / "r-1" / "host" / "audit" / "t-loc-0001"
        self.assertTrue((host / A.STATE).is_file())
        self.assertFalse((host / THR.tvl.SID / "r1.render.part1.md").exists())
        block = receipt["audit"]
        self.assertEqual((block["source"], block["documents"], block["audited"]),
                         ("fetched", 1, 1))
        self.assertEqual(block["outcomes"]["approved"], 1)
        self.assertEqual(block["cost_usd"]["audit"], 2.0)
        self.assertTrue(receipt["complete"])
        self.assertIn("audit: rule 1, 2 review round(s); 1 of 1 document(s) audited", out)
        # nothing of the audit lands in the corpus's manifest
        self.assertEqual(sorted(receipt["manifest"]), [f"t-loc-0001/{THR.tvl.SID}.json"])
        # a local re-harvest reads the copy the fetch left (kept)
        code, out = self.harvest("--from", str(self.remote / "r-1" / "findings"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.receipt()["audit"]["source"], "kept")
        self.assertEqual(self.receipt()["audit"]["outcomes"]["approved"], 1)

    def test_an_audited_run_without_its_audit_directory_is_incomplete(self):
        self.audited()
        shutil.rmtree(self.remote / "r-1-tend" / "audit")
        code, out = self.harvest("--execute")
        self.assertEqual(code, 1, out)
        receipt = self.receipt()
        self.assertEqual(receipt["host_artifacts_missing"], ["audit"])
        self.assertIsNone(receipt["audit"]["source"])
        self.assertEqual(receipt["audit"]["outcomes"]["unaudited"], 1)
        self.assertIn("no audit directory is here", out)

    def test_an_unaudited_run_fetches_no_audit_and_gets_no_block(self):
        (self.remote / "r-1-tend" / "audit").mkdir(parents=True)
        code, out = self.harvest("--execute")
        self.assertEqual(code, 0, out)
        receipt = self.receipt()
        self.assertNotIn("audit", receipt)
        self.assertNotIn("audit", receipt["host_artifacts"])
        self.assertNotIn("audit:", out)


class TestTheRemoteFiles(unittest.TestCase):

    def test_the_audit_row_only_for_an_audited_run(self):
        ws = type("W", (), {})()
        ws.paths = type("P", (), {"gate_json": lambda self, run: "/nonexistent/gate.json",
                                  "campaign_yaml": lambda self, c: Path("/nonexistent")})()
        base = {"host": {"remote_root": "/srv/r"}, "remote": {"tend_root": "r-1-tend"},
                "locate": {"mode": "locate-inventory"}}
        plain = host_mod.remote_files(ws, "r-1", base)
        # an unaudited run's turn records instead (harvest.turn_records)
        self.assertEqual([f["name"] for f in plain],
                         [host_mod.ENVELOPES, host_mod.CONTRACTS, host_mod.NEEDS_REVIEW,
                          host_mod.TAMPER])
        audited = host_mod.remote_files(ws, "r-1", {**base, "locate": AUDITED["locate"]})
        self.assertEqual(audited[:-1], plain[:1])
        self.assertEqual(audited[-1], {"name": "audit", "remote": "/srv/r/r-1-tend/audit",
                                       "dir": True, "expected": True})

    def test_build_receipt_records_the_block_only_when_given(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        write(tmp / "f.json", {"findings": []})
        args = dict(run_id="r", source={"kind": "local", "path": str(tmp)}, expected=["f.json"],
                    dest=tmp)
        self.assertNotIn("audit", harvest_mod.build_receipt(**args))
        self.assertEqual(harvest_mod.build_receipt(**args, audit={"rule": 1})["audit"],
                         {"rule": 1})


if __name__ == "__main__":
    unittest.main()
