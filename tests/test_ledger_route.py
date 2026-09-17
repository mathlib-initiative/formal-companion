"""Regression tests for the ledger, routing, transcription and report stages.

Run:

    python3 -m unittest discover -s /Users/jack/Desktop/companion-faithfulness/tests -v

The tests that read the real audit read it through environment variables and
skip cleanly when they are unset:

    CF_BASELINE_CORPUS=/Users/jack/Desktop/tao-diff-run      the 2,672-file corpus
    CF_BASELINE_DIFF=/Users/jack/Desktop/tao-differential    batch 1 + the adam ledger

Scratch workspaces are created under ``$CF_TEST_TMP`` when it is set (point it
at a scratch directory), else under the system temp directory.  Nothing in here
writes to a baseline, a companion repository or a workspace that exists.

The two load-bearing facts they pin, both recomputed from the baseline:

* the hand-written adam ledger (10 accepted + 5 refuted v1 rows) maps onto 11
  v2 rows that answer 11 real findings and validate with zero errors;
* routing the baseline corpus for ``adam`` yields 7 rows over 5 items, is
  UNCHALLENGED, and refuses to emit anything upstream.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL_ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from ledger import compat, migrate, transcribe, workflow_args  # noqa: E402
from ledger import findings as findings_mod  # noqa: E402
from ledger import validate_ledger as vl  # noqa: E402
from report import claims as claims_mod  # noqa: E402
from report import make_handoff as handoff_mod  # noqa: E402
from route import route as route_mod  # noqa: E402

BASELINE_CORPUS = os.environ.get("CF_BASELINE_CORPUS", "")
BASELINE_DIFF = os.environ.get("CF_BASELINE_DIFF", "")
BASELINE_RUNS = ("findings", "findings-pdf", "findings-gpt", "findings-vac", "findings-hunt")
#: the two Tao editions, by the ids registry/sources.yaml gives them
PUBLISHED_ID = "tao-measure-gsm126-published"
DRAFT_ID = "tao-measure-2011-draft"
#: the edition every batch-1 ledger row was judged against
OFFICIAL_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"
DRAFT_SHA = "62d95658" + "0" * 56
#: what the workspace knows, as validate_row/route want it
SOURCES = {PUBLISHED_ID: OFFICIAL_SHA, DRAFT_ID: DRAFT_SHA}


def workspace_yaml(*, companions: str, name: str = "tao") -> str:
    """A v2 workspace.yaml holding BOTH Tao editions.

    Every stage fixture uses the same two sources, so a test that means "the
    other edition" has one to name.
    """
    return (
        "version: 2\n"
        "workspace:\n  name: %s\n"
        "companions:\n%s"
        "sources:\n"
        "  %s:\n"
        '    sha256: "%s"\n'
        "    title: An Introduction to Measure Theory\n"
        '    edition: "AMS GSM 126 (published pagination)"\n'
        "    offset: 16\n"
        "    errata_url: https://example.invalid/errata\n"
        "    role: audited\n"
        "  %s:\n"
        '    sha256: "%s"\n'
        "    title: An Introduction to Measure Theory\n"
        '    edition: "2011-01-24 draft"\n'
        "    offset: 16\n"
        "    role: reference\n"
        "hosts:\n  allowlist: []\n"
        % (name, companions, PUBLISHED_ID, OFFICIAL_SHA, DRAFT_ID, DRAFT_SHA)
    )


def scratch_dir() -> str:
    """A throwaway workspace directory (under $CF_TEST_TMP when set)."""
    base = os.environ.get("CF_TEST_TMP") or None
    if base:
        os.makedirs(base, exist_ok=True)
    # realpath: on macOS /var/folders is a symlink to /private/var/folders and
    # cflib resolves the paths it builds, so an unresolved root breaks prefix checks
    return os.path.realpath(tempfile.mkdtemp(prefix="cf-test-", dir=base))


def corpus_dirs() -> list[str]:
    return [os.path.join(BASELINE_CORPUS, d) for d in BASELINE_RUNS]


def write_corpus(root: str, run_id: str, docs: list[dict]) -> str:
    """Write a synthetic corpus run: one JSON file per book item."""
    directory = os.path.join(root, "corpus", run_id)
    os.makedirs(directory, exist_ok=True)
    for doc in docs:
        slug = doc["item"].lower().replace(" ", "-").replace(".", "-")
        compat.write_json(os.path.join(directory, "%s.json" % slug), doc)
    return directory


def finding_doc(item: str, **row) -> dict:
    """One corpus document with a single disagreement row."""
    row.setdefault("concept", "the concept")
    row.setdefault("kind", "CONTENT")
    row.setdefault("severity", "low")
    row.setdefault("repos_involved", ["ours", "adam"])
    row.setdefault("faithful", ["ours"])
    row.setdefault("reasoning", "because the binder differs")
    row.setdefault("book_quote", "Let [a, b] be an interval.")
    return {"item": item, "book_located": True, "source_id": PUBLISHED_ID, "source_sha": OFFICIAL_SHA,
            "matches": [], "coverage_notes": "", "disagreements": [row]}


def _abs_paths(payload, out=None) -> list[str]:
    """Every absolute-looking path string anywhere in a workflow args object."""
    out = [] if out is None else out
    if isinstance(payload, dict):
        for value in payload.values():
            _abs_paths(value, out)
    elif isinstance(payload, list):
        for value in payload:
            _abs_paths(value, out)
    elif isinstance(payload, str) and payload.startswith("/") and " " not in payload:
        out.append(payload)
    return out


def ledger_row(**over) -> dict:
    """A minimal valid v2 ledger row."""
    row = {
        "finding_id": "a" * 64,
        "item": "Exercise 1.1.1",
        "repo": "adam",
        "kind": "CONTENT",
        "unfaithful_decls": ["adam:Foo.bar"],
        "verdict": "CONFIRMED_CONTENT",
        "severity": "low",
        "confidence": 0.9,
        "contested": False,
        "dissent": [],
        "reason": "the Lean statement excludes the degenerate interval the book admits",
        "method": "workflow:adjudicate",
        "source_id": PUBLISHED_ID, "source_sha": OFFICIAL_SHA,
    }
    row.update(over)
    return row


class TestLedgerValidationOnBaseline(unittest.TestCase):
    """The hand-written adam ledger must survive the machine validator."""

    @classmethod
    def setUpClass(cls):
        if not BASELINE_DIFF:
            raise unittest.SkipTest("CF_BASELINE_DIFF is unset")
        harvest = os.path.join(BASELINE_DIFF, "harvest.partial.json")
        if not os.path.exists(harvest):
            raise unittest.SkipTest("no harvest.partial.json in CF_BASELINE_DIFF")
        # The corpus is stamped with the source at load: `ledger_coverage` joins
        # on (source_id, finding_id), so findings loaded without a source and
        # rows migrated with one would never meet.
        cls.findings = findings_mod.load_findings(
            [harvest], run_id="batch1-local",
            source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA)
        cls.index = findings_mod.index_by_id(cls.findings)
        accepted, _ = migrate.map_v1_file(
            os.path.join(BASELINE_DIFF, "accepted.adam.jsonl"),
            repo="adam", source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA,
            findings=cls.findings, run_id="batch1-local")
        # no source_sha here on purpose: refuted.adam.jsonl records the pinned
        # edition in its own _meta.official_pdf.sha256, and the migration reads it
        refuted, meta = migrate.map_v1_file(
            os.path.join(BASELINE_DIFF, "refuted.adam.jsonl"),
            repo="adam", source_id=PUBLISHED_ID, findings=cls.findings,
            run_id="batch1-local")
        cls.meta = meta
        cls.rows = migrate.merge_rows(accepted + refuted)

    def test_v1_files_map_onto_linked_v2_rows(self):
        self.assertEqual(len(self.rows), 11, "10 accepted + 5 refuted v1 rows, 4 overlapping")
        for row in self.rows:
            self.assertIn(row["finding_id"], self.index, "row %s is unlinked" % row["item"])
            self.assertEqual(row["source_sha"], OFFICIAL_SHA)
            self.assertEqual(row["repo"], "adam")
        verdicts = {}
        for row in self.rows:
            verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
        self.assertEqual(verdicts["CONFIRMED_CONTENT"], 4)
        self.assertEqual(verdicts["ATTRIBUTION_GAP"], 2)
        self.assertEqual(verdicts["CONVENTION"], 4)
        self.assertEqual(verdicts["REFUTED"], 1)

    def test_baseline_ledger_validates_with_no_errors(self):
        errs = vl.validate_rows(self.rows, index=self.index, sources=SOURCES)
        self.assertEqual(errs, [], "\n".join(str(e) for e in errs))

    def test_every_convention_row_carries_its_recovery_argument(self):
        for row in self.rows:
            if row["verdict"] == "CONVENTION":
                self.assertGreaterEqual(len(row.get("recovery_argument", "")),
                                        vl.MIN_RECOVERY_CHARS, row["item"])

    def test_resolution_grammar_is_normalised(self):
        # "refuted; binder dropped in PR#10" is not a legal token on its own
        notes = [r for r in self.rows if r.get("resolution_note")]
        self.assertTrue(notes, "expected at least one row with trailing prose")
        for row in self.rows:
            self.assertTrue(compat.resolution_ok(row.get("resolution")), row.get("resolution"))

    def test_ledger_coverage_answers_every_adam_finding(self):
        adam = findings_mod.rows_naming(self.findings, "adam")
        cov = vl.ledger_coverage(self.rows, adam)
        self.assertEqual(cov["missing"], 0, cov["missing_items"])
        self.assertEqual(cov["covered"], 11)

    # -- the corruptions the validator must catch -------------------------
    def _first(self, verdict: str) -> dict:
        for row in self.rows:
            if row["verdict"] == verdict:
                return dict(row)
        self.fail("no %s row in the baseline ledger" % verdict)

    def _codes(self, row: dict) -> set:
        errs = vl.validate_rows([row], index=self.index, sources=SOURCES)
        return {e["code"] for e in errs}

    def test_corrupted_verdict_fails(self):
        row = self._first("CONFIRMED_CONTENT")
        row["verdict"] = "PROBABLY_FINE"
        self.assertIn("verdict", self._codes(row))

    def test_convention_without_recovery_argument_fails(self):
        row = self._first("CONVENTION")
        row["recovery_argument"] = "looks fine"
        self.assertIn("recovery_argument", self._codes(row))

    def test_row_answering_no_finding_fails(self):
        row = self._first("CONFIRMED_CONTENT")
        row["finding_id"] = "0" * 64
        self.assertIn("unlinked", self._codes(row))

    def test_wrong_edition_fails(self):
        row = self._first("CONFIRMED_CONTENT")
        row["source_sha"] = DRAFT_SHA  # the 2011 draft
        self.assertIn("source_sha", self._codes(row))

    def test_declaration_the_finding_never_named_fails(self):
        row = self._first("CONFIRMED_CONTENT")
        row["unfaithful_decls"] = ["adam:Totally.Invented.Name"]
        self.assertIn("decl_mismatch", self._codes(row))

    def test_illegal_resolution_fails(self):
        row = self._first("CONFIRMED_CONTENT")
        row["resolution"] = "fixed in some PR"
        self.assertIn("resolution", self._codes(row))

    def test_row_filed_under_the_wrong_repo_fails(self):
        row = self._first("CONFIRMED_CONTENT")
        row["repo"] = "ours"
        self.assertIn("repo", self._codes(row))

    def test_contradictory_rows_for_one_finding_fail(self):
        row = self._first("CONFIRMED_CONTENT")
        other = dict(row)
        other["verdict"] = "REFUTED"
        other["severity"] = "none"
        errs = vl.validate_rows([row, other], index=self.index, sources=SOURCES)
        self.assertIn("contradiction", {e["code"] for e in errs})

    def test_severity_none_on_an_open_verdict_fails(self):
        row = self._first("CONFIRMED_CONTENT")
        row["severity"] = "none"
        self.assertIn("severity", self._codes(row))

    def test_missing_dissent_argument_fails(self):
        row = self._first("CONFIRMED_CONTENT")
        row["dissent"] = [{"role": "skeptic-convention", "confidence": 0.6,
                           "proposed_verdict": "CONVENTION", "argument": ""}]
        self.assertIn("dissent", self._codes(row))


class TestRoutingTheBaselineCorpus(unittest.TestCase):
    """Routing must key on unfaithful_decls and refuse unchallenged exports."""

    @classmethod
    def setUpClass(cls):
        if not BASELINE_CORPUS:
            raise unittest.SkipTest("CF_BASELINE_CORPUS is unset")
        missing = [d for d in corpus_dirs() if not os.path.isdir(d)]
        if missing:
            raise unittest.SkipTest("missing baseline runs: %s" % ", ".join(missing))
        cls.findings = findings_mod.load_findings(
            corpus_dirs(), source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA)

    def test_corpus_row_count(self):
        self.assertEqual(len(self.findings), 347)

    def test_adam_routes_to_seven_rows_over_five_items(self):
        routed = route_mod.route_repo(self.findings, "adam", source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA,
                                      workspace="/nonexistent")
        counts = routed["meta"]["counts"]
        self.assertEqual(counts["rows"], 7)
        self.assertEqual(counts["items"], 5)
        self.assertEqual(counts["withdrawn"], 0)
        self.assertEqual(counts["unchallenged"], 7)
        self.assertEqual(routed["meta"]["status"], route_mod.STATUS_UNCHALLENGED)
        self.assertEqual(routed["meta"]["rule"], route_mod.ROUTING_RULE)
        for row in routed["rows"]:
            self.assertTrue(any(d.startswith("adam:") for d in row["unfaithful_decls"]))

    def test_other_companions_match_the_published_tallies(self):
        # The published tally counts every row; routing then WITHDRAWS the
        # LLM-emitted PAGE_ANCHOR rows (22 of ours' 40 items were pagination),
        # so rows + withdrawn is what has to match 72/40 and 147/71.
        for repo, rows, items, open_rows in (("ours", 72, 40, 31), ("teorth", 147, 71, 140)):
            routed = route_mod.route_repo(self.findings, repo, source_id=PUBLISHED_ID,
                                       source_sha=OFFICIAL_SHA)
            counts = routed["meta"]["counts"]
            self.assertEqual(len(routed["rows"]), rows, repo)
            self.assertEqual(len({r["item"] for r in routed["rows"]}), items, repo)
            self.assertEqual(counts["rows"], open_rows, repo)
            self.assertEqual(counts["rows"] + counts["withdrawn"], rows, repo)
            for row in routed["rows"]:
                if row.get("withdrawn"):
                    self.assertEqual(row["kind"], "PAGE_ANCHOR", repo)

    def test_upstream_export_is_refused_while_unchallenged(self):
        routed = route_mod.route_repo(self.findings, "adam", source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA,
                                      workspace="/camp")
        out = scratch_dir()
        try:
            with self.assertRaises(route_mod.UpstreamRefused) as ctx:
                route_mod.emit_upstream(routed, out, companion="adam", workspace="/camp")
            message = str(ctx.exception)
            self.assertIn("UNCHALLENGED", message)
            self.assertIn("--challenge-only", message, "the refusal must print the next command")
            self.assertEqual(os.listdir(out), [], "nothing may be written on a refusal")
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_owner_message_is_a_draft_that_names_the_status(self):
        routed = route_mod.route_repo(self.findings, "adam", source_id=PUBLISHED_ID,
                                      source_sha=OFFICIAL_SHA, workspace="/c")
        text = route_mod.owner_message(routed, "adam", owner="external")
        self.assertIn("DRAFT", text)
        self.assertIn("UNCHALLENGED", text)
        self.assertIn("must not be sent", text)


class TestRoutingRules(unittest.TestCase):
    """The rules that changed relative to the recovered routing cell."""

    def setUp(self):
        self.rows = findings_mod.load_findings([], run_id="synthetic")

    def _findings(self, docs):
        root = scratch_dir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        directory = write_corpus(root, "synthetic", docs)
        return findings_mod.load_findings(
            [directory], source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA), root

    def test_the_fallback_branch_is_gone(self):
        # ours is "involved" and is not the faithful repo, but is named nowhere:
        # the recovered cell routed this row to ours; the rule now says no.
        rows, _ = self._findings([
            finding_doc("Exercise 1.2.4", repos_involved=["ours", "adam"], faithful=["adam"],
                        unfaithful_decls=["adam:Some.decl"]),
        ])
        self.assertEqual(route_mod.route_repo(rows, "ours")["meta"]["counts"]["rows"], 0)
        self.assertEqual(route_mod.route_repo(rows, "adam")["meta"]["counts"]["rows"], 1)

    def test_llm_page_anchor_rows_are_withdrawn_with_the_command_to_redo_them(self):
        rows, _ = self._findings([
            finding_doc("Exercise 1.1.19", kind="PAGE_ANCHOR",
                        unfaithful_decls=["adam:Jordan.split"]),
        ])
        routed = route_mod.route_repo(rows, "adam")
        self.assertEqual(routed["meta"]["counts"]["rows"], 0)
        self.assertEqual(routed["meta"]["counts"]["withdrawn"], 1)
        self.assertIn("mechanical-only", routed["rows"][0]["withdrawn_reason"])

    def test_a_mechanical_anchor_row_is_the_oracle_and_always_routes(self):
        """critique.json correction 2: route anchor_check rows, do not withdraw them."""
        for key in ("source", "provenance"):   # the new spelling and the legacy one
            rows, _ = self._findings([
                finding_doc("Exercise 1.1.19", kind="PAGE_ANCHOR", severity="low",
                            unfaithful_decls=["adam:Jordan.split"], **{key: "anchor_check"}),
            ])
            check = {"rows": [{"item": "Exercise 1.1.19"}],
                     "summary": {"checked_items": ["Exercise 1.1.19"]}}
            routed = route_mod.route_repo(rows, "adam", anchor_check=check)
            self.assertEqual(routed["meta"]["counts"]["rows"], 1, key)
            self.assertEqual(routed["meta"]["counts"]["withdrawn"], 0, key)

    def test_an_llm_anchor_row_the_edition_confirms_is_routed(self):
        rows, _ = self._findings([
            finding_doc("Exercise 1.1.19", kind="PAGE_ANCHOR", severity="low",
                        unfaithful_decls=["adam:Jordan.split"]),
        ])
        check = {"rows": [{"item": "Exercise 1.1.19"}],
                 "summary": {"checked_items": ["Exercise 1.1.19"]}}
        routed = route_mod.route_repo(rows, "adam", anchor_check=check)
        self.assertEqual(routed["meta"]["counts"]["withdrawn"], 0)

    def test_an_llm_anchor_row_the_edition_contradicts_is_withdrawn(self):
        """Examined by anchor_check and NOT mismatched -> the LLM row is wrong."""
        rows, _ = self._findings([
            finding_doc("Exercise 1.1.19", kind="PAGE_ANCHOR", severity="low",
                        unfaithful_decls=["adam:Jordan.split"]),
        ])
        check = {"rows": [], "summary": {"checked_items": ["Exercise 1.1.19"]}}
        routed = route_mod.route_repo(rows, "adam", anchor_check=check)
        self.assertEqual(routed["meta"]["counts"]["withdrawn"], 1)
        self.assertIn("contradicted by the pinned edition",
                      routed["rows"][0]["withdrawn_reason"])

    def test_an_llm_anchor_row_on_an_unexamined_item_says_so(self):
        rows, _ = self._findings([
            finding_doc("Exercise 1.1.19", kind="PAGE_ANCHOR", severity="low",
                        unfaithful_decls=["adam:Jordan.split"]),
        ])
        routed = route_mod.route_repo(rows, "adam", anchor_check={"rows": [], "summary": {}})
        self.assertEqual(routed["meta"]["counts"]["withdrawn"], 1)
        self.assertIn("never examined", routed["rows"][0]["withdrawn_reason"])

    def test_anchor_check_writes_the_keys_the_gate_and_route_read(self):
        """One provenance key and a structured page, or the gate refuses its own output."""
        from gate import verify_findings as vf
        from pair import anchor_check as ac
        from plan import presets as presets_mod

        items = {"Exercise 1.1.19": {"page": 40, "pdf_page": 56, "occurrences": []}}
        tables = {"adam": [{"item": "Exercise 1.1.19", "decl": "Jordan.split",
                            "page_anchor": "23", "file": "A.lean", "line": 1}]}
        result = ac.check(items, tables, source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA,
                          offset=16, run_id="anchor-check")
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["source"], ac.SOURCE)
        self.assertEqual(route_mod.provenance(row), route_mod.MECHANICAL_SOURCE)
        self.assertEqual(row["page"], {"claimed": [23], "actual": 40, "actual_pdf_page": 56})
        self.assertEqual(result["summary"]["checked_items"], ["Exercise 1.1.19"])

        # ... and the row, wrapped as a corpus document, passes the mechanical gate
        from detect import corpus as mech_corpus

        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        book = root / "book.txt"
        book.write_text("Exercise 1.1.19 is on this page.\n", encoding="utf-8")
        cfg = vf.GateConfig(presets_mod.gate_config(
            presets_mod.get("mechanical"), run_id="anchor-check",
            sources={PUBLISHED_ID: {"text": str(book), "sha256": OFFICIAL_SHA,
                                    "page_offset": 16}},
            repos=["adam"]))
        books, errs = vf.load_sources(cfg)
        self.assertEqual(errs, [])
        docs = mech_corpus.documents(result["rows"], companion="adam", run_id="anchor-check",
                                     source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA)
        problems = []
        for doc in docs:
            self.assertEqual(doc["source_id"], PUBLISHED_ID)
            self.assertEqual(doc["source_sha"], OFFICIAL_SHA)
            problems += vf.validate_doc(doc, "x.json", cfg, books)
        self.assertEqual(problems, [])

    def test_status_becomes_challenged_once_every_row_has_a_ledger_verdict(self):
        rows, root = self._findings([
            finding_doc("Exercise 1.2.4", unfaithful_decls=["adam:Some.decl"]),
        ])
        ledger = [ledger_row(finding_id=rows[0]["finding_id"], item=rows[0]["item"],
                             unfaithful_decls=["adam:Some.decl"])]
        routed = route_mod.route_repo(rows, "adam", source_id=PUBLISHED_ID,
                                      source_sha=OFFICIAL_SHA, ledger_rows=ledger)
        self.assertEqual(routed["meta"]["status"], route_mod.STATUS_CHALLENGED)
        out = os.path.join(root, "outbox")
        path = route_mod.emit_upstream(routed, out, companion="adam", redact_quotes=True)
        exported = compat.data_rows(compat.read_jsonl(path))
        self.assertEqual(len(exported), 1)
        self.assertTrue(exported[0]["book_quote"]["redacted"])
        self.assertEqual(exported[0]["ledger_verdict"], "CONFIRMED_CONTENT")

    def test_unsure_rows_are_held_back_from_the_upstream_export(self):
        rows, root = self._findings([
            finding_doc("Exercise 1.2.4", unfaithful_decls=["adam:Some.decl"]),
        ])
        ledger = [ledger_row(finding_id=rows[0]["finding_id"], item=rows[0]["item"],
                             unfaithful_decls=["adam:Some.decl"], verdict="UNSURE")]
        routed = route_mod.route_repo(rows, "adam", source_id=PUBLISHED_ID,
                                      source_sha=OFFICIAL_SHA, ledger_rows=ledger)
        self.assertEqual(routed["meta"]["status"], route_mod.STATUS_CHALLENGED)
        path = route_mod.emit_upstream(routed, os.path.join(root, "out"), companion="adam")
        raw = compat.read_jsonl(path)
        self.assertEqual(compat.data_rows(raw), [])
        self.assertEqual(compat.meta_row(raw)["counts"]["held"], 1)

    def test_export_refuses_one_source_id_carrying_two_shas(self):
        """That means two editions were routed as if they were one source.

        It is how batch 1's anchor findings ended up judged against the 2011
        draft while claiming to be about the published book.
        """
        rows, root = self._findings([
            finding_doc("Exercise 1.2.4", unfaithful_decls=["adam:Some.decl"]),
        ])
        rows[0]["source_sha"] = DRAFT_SHA                      # ... under the published id
        rows.append(dict(rows[0], source_sha=OFFICIAL_SHA, finding_id="b" * 64))
        ledger = [ledger_row(finding_id=r["finding_id"], item=r["item"],
                             unfaithful_decls=["adam:Some.decl"]) for r in rows]
        routed = route_mod.route_repo(rows, "adam", ledger_rows=ledger)
        self.assertEqual(routed["meta"]["source_sha_conflict"], [PUBLISHED_ID])
        with self.assertRaises(route_mod.UpstreamRefused) as cm:
            route_mod.emit_upstream(routed, os.path.join(root, "out"), companion="adam")
        self.assertIn(PUBLISHED_ID, str(cm.exception))

    def test_export_allows_rows_from_two_DIFFERENT_sources(self):
        """They say which is which, so nothing is being laundered."""
        rows, root = self._findings([
            finding_doc("Exercise 1.2.4", unfaithful_decls=["adam:Some.decl"]),
        ])
        rows.append(dict(rows[0], source_id=DRAFT_ID, source_sha=DRAFT_SHA,
                         finding_id="b" * 64))
        ledger = [ledger_row(finding_id=r["finding_id"], item=r["item"],
                             unfaithful_decls=["adam:Some.decl"]) for r in rows]
        routed = route_mod.route_repo(rows, "adam", ledger_rows=ledger)
        self.assertNotIn("source_sha_conflict", routed["meta"])
        self.assertEqual(routed["meta"]["sources"],
                         {PUBLISHED_ID: OFFICIAL_SHA, DRAFT_ID: DRAFT_SHA})
        self.assertEqual(routed["meta"]["counts"]["by_source"],
                         {PUBLISHED_ID: 1, DRAFT_ID: 1})
        path = route_mod.emit_upstream(routed, os.path.join(root, "out"), companion="adam")
        out = compat.data_rows(compat.read_jsonl(path))
        self.assertEqual(sorted(r["source_id"] for r in out), sorted([DRAFT_ID, PUBLISHED_ID]))

    def test_refuted_rows_are_not_exported_upstream(self):
        rows, root = self._findings([
            finding_doc("Exercise 1.2.4", unfaithful_decls=["adam:Some.decl"]),
        ])
        ledger = [ledger_row(finding_id=rows[0]["finding_id"], item=rows[0]["item"],
                             unfaithful_decls=["adam:Some.decl"], verdict="REFUTED",
                             severity="none")]
        routed = route_mod.route_repo(rows, "adam", source_id=PUBLISHED_ID,
                                      source_sha=OFFICIAL_SHA, ledger_rows=ledger)
        path = route_mod.emit_upstream(routed, os.path.join(root, "out"), companion="adam")
        self.assertEqual(compat.data_rows(compat.read_jsonl(path)), [])

    def test_routed_file_round_trips_through_disk(self):
        rows, root = self._findings([
            finding_doc("Exercise 1.2.4", unfaithful_decls=["adam:Some.decl"]),
        ])
        paths = compat.workspace_paths(root)
        routed = route_mod.route_repo(rows, "adam", source_id=PUBLISHED_ID,
                                      source_sha=OFFICIAL_SHA, workspace=root)
        path = route_mod.write_routed(paths, "adam", routed)
        again = route_mod.read_routed(path)
        self.assertEqual(again["meta"]["status"], route_mod.STATUS_UNCHALLENGED)
        self.assertEqual(len(again["rows"]), 1)
        self.assertEqual(again["rows"][0]["finding_id"], rows[0]["finding_id"])


class TestTranscription(unittest.TestCase):
    """Workflow results become ledger rows, files and the record markdown."""

    def setUp(self):
        self.root = scratch_dir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.dir = write_corpus(self.root, "synthetic", [
            finding_doc("Exercise 1.3.12", unfaithful_decls=["adam:Tao.witness"]),
            finding_doc("Exercise 1.1.22", unfaithful_decls=["adam:Tao.darboux"]),
        ])
        self.findings = findings_mod.load_findings(
            [self.dir], source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA)
        self.index = findings_mod.index_by_id(self.findings)
        self.by_item = {f["item"]: f for f in self.findings}

    def _result(self) -> dict:
        a = self.by_item["Exercise 1.3.12"]
        b = self.by_item["Exercise 1.1.22"]
        return {
            "schema": "cf-adjudicate-v2",
            "companion": "adam",
            "source_id": PUBLISHED_ID, "source_sha": OFFICIAL_SHA,
            "findings": [
                {
                    "finding_id": a["finding_id"], "item": a["item"],
                    "reconciled": {"item": a["item"], "verdict": "CONFIRMED_CONTENT",
                                   "severity": "low", "confidence": 0.8,
                                   "rationale": "the witness is fixed at d = 1",
                                   "pr_plan": "generalise to every d >= 1",
                                   "fix_effort": "small", "pr_recommended": True},
                    "skeptics": [
                        {"role": "skeptic-content", "overturned": False, "confidence": 0.2,
                         "argument": "no stronger reading", "proposed_verdict": "CONFIRMED_CONTENT"},
                        {"role": "skeptic-convention", "overturned": True, "confidence": 0.6,
                         "argument": "the standing 1 <= d convention may cover it",
                         "proposed_verdict": "CONVENTION"},
                    ],
                    "lenses": [{"lens": "book", "verdict": "CONFIRMED_CONTENT", "confidence": 0.7}],
                    "contested": False,
                },
                {
                    "finding_id": b["finding_id"], "item": b["item"],
                    "reconciled": {"item": b["item"], "verdict": "CONVENTION", "severity": "low",
                                   "confidence": 0.92,
                                   "rationale": "the degenerate interval is settled by fiat",
                                   "recovery_argument": "Definition 1.1.5 removes a = b and fixes "
                                                        "the value, so the a < b binder loses nothing",
                                   "fix_effort": "none", "pr_recommended": False},
                    "skeptics": [
                        {"role": "skeptic-content", "overturned": True, "confidence": 0.8,
                         "argument": "the degenerate case is a real exclusion",
                         "proposed_verdict": "CONFIRMED_CONTENT"},
                    ],
                    "contested": False,
                },
            ],
            "definitions": [], "uncovered": [], "assessments": [],
            "critic": "nothing contradicts anything",
        }

    def test_adjudication_result_becomes_two_files_and_a_record(self):
        result_path = os.path.join(self.root, "result.json")
        compat.write_json(result_path, self._result())
        report = transcribe.transcribe(result_path, workspace=self.root, companion="adam",
                                       source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA, index=self.index, at="2026-09-16")
        self.assertEqual(report["errors"], [], "\n".join(str(e) for e in report["errors"]))
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["refuted"], 1)
        self.assertEqual(report["contested"], 1, "a skeptic at 0.8 is above the threshold")

        paths = compat.workspace_paths(self.root)
        accepted = compat.data_rows(compat.read_jsonl(paths.accepted("adam")))
        refuted = compat.data_rows(compat.read_jsonl(paths.refuted("adam")))
        self.assertEqual(accepted[0]["verdict"], "CONFIRMED_CONTENT")
        self.assertEqual(refuted[0]["verdict"], "CONVENTION")
        self.assertIn("Definition 1.1.5", refuted[0]["recovery_argument"])
        # sub-threshold dissent is recorded even though the row is not contested
        self.assertEqual(len(accepted[0]["dissent"]), 1)
        self.assertAlmostEqual(accepted[0]["dissent"][0]["confidence"], 0.6)
        self.assertFalse(accepted[0]["contested"])

        with open(paths.adjudicated_md("adam"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("Exercise 1.3.12", md)
        self.assertIn("Dissent", md)
        self.assertIn("2026-09-16", md)

    def test_transcription_is_idempotent(self):
        result_path = os.path.join(self.root, "result.json")
        compat.write_json(result_path, self._result())
        kwargs = dict(workspace=self.root, companion="adam", source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA,
                      index=self.index, at="2026-09-16")
        transcribe.transcribe(result_path, **kwargs)
        paths = compat.workspace_paths(self.root)
        with open(paths.accepted("adam"), encoding="utf-8") as fh:
            first = fh.read()
        transcribe.transcribe(result_path, **kwargs)
        with open(paths.accepted("adam"), encoding="utf-8") as fh:
            self.assertEqual(first, fh.read())

    def test_challenge_result_defaults_to_refuted(self):
        a = self.by_item["Exercise 1.3.12"]
        b = self.by_item["Exercise 1.1.22"]
        result = {
            "schema": "cf-challenge-v2", "companion": "adam", "source_id": PUBLISHED_ID, "source_sha": OFFICIAL_SHA,
            "findings": [
                {"finding_id": a["finding_id"], "item": a["item"], "survives_all": True,
                 "verdicts": [{"ruling_survives": True, "classification": "CONTENT",
                               "evidence": "file:line and the book sentence"},
                              {"ruling_survives": True, "classification": "CONTENT",
                               "evidence": "independently the same"}]},
                {"finding_id": b["finding_id"], "item": b["item"], "survives_all": False,
                 "verdicts": [{"ruling_survives": False, "classification": "CONVENTION",
                               "evidence": "Definition 1.1.5 settles the degenerate case by fiat, "
                                           "so nothing is lost by the a < b binder"},
                              {"ruling_survives": True, "classification": "CONTENT",
                               "evidence": "reads as a genuine exclusion"}]},
            ],
        }
        path = os.path.join(self.root, "challenge.json")
        compat.write_json(path, result)
        report = transcribe.transcribe(path, workspace=self.root, companion="adam",
                                       source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA, index=self.index,
                                       kind="challenge", at="2026-09-16")
        self.assertEqual(report["errors"], [], "\n".join(str(e) for e in report["errors"]))
        paths = compat.workspace_paths(self.root)
        accepted = compat.data_rows(compat.read_jsonl(paths.accepted("adam")))
        refuted = compat.data_rows(compat.read_jsonl(paths.refuted("adam")))
        self.assertEqual([r["verdict"] for r in accepted], ["CONFIRMED_CONTENT"])
        self.assertEqual([r["verdict"] for r in refuted], ["CONVENTION"])
        self.assertTrue(refuted[0]["contested"], "a split panel is contested")
        self.assertEqual(refuted[0]["method"], "workflow:challenge")

    def test_a_missing_vote_never_lets_a_ruling_survive(self):
        a = self.by_item["Exercise 1.3.12"]
        rows = transcribe.rows_from_challenge(
            {"findings": [{"finding_id": a["finding_id"], "item": a["item"],
                           "survives_all": False, "missing_votes": 1,
                           "verdicts": [{"ruling_survives": True, "classification": "CONTENT",
                                         "evidence": "only one refuter answered"}]}]},
            companion="adam", source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA, index=self.index)
        self.assertEqual(rows[0]["verdict"], "REFUTED")


class TestWorkflowArgs(unittest.TestCase):
    """`cf adjudicate --emit-args` must produce workspace-only, id-linked inputs."""

    def setUp(self):
        self.root = scratch_dir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        paths = compat.workspace_paths(self.root)
        compat.write_jsonl(paths.statements("adam"), [
            {"item": "Exercise 1.1.22", "decl": "Tao.darboux", "file": "Riemann/Basic.lean",
             "line": 2979, "statement": "theorem Tao.darboux (hab : a < b) : ...", "sorry": False},
        ])
        compat.write_jsonl(paths.statements("ours"), [
            {"item": "Exercise 1.1.22", "decl": "darbouxIntegrable_iff", "file": "Section_1_1.lean",
             "line": 120, "statement": "theorem darbouxIntegrable_iff (hab : a <= b) : ...",
             "sorry": True},
        ])
        with open(paths.conventions("adam"), "w", encoding="utf-8") as fh:
            fh.write("- elementary sets are a subtype\n- 1 <= d is a standing hypothesis\n")
        compat.write_text(
            os.path.join(self.root, "workspace.yaml"),
            workspace_yaml(companions=(
                "  adam:\n    repo: /repos/measure_theory\n    owner: external\n"
                "    adapter: attribution_table\n"
                f"    sources: [{PUBLISHED_ID}]\n"
                f"    anchor_source: {PUBLISHED_ID}\n"
                "  ours:\n    repo: /repos/tao-measure-lean\n"
                "    adapter: docstring_tags\n"
                f"    sources: [{PUBLISHED_ID}, {DRAFT_ID}]\n"
                f"    anchor_source: {PUBLISHED_ID}\n")))
        self.dir = write_corpus(self.root, "synthetic", [
            finding_doc("Exercise 1.1.22", unfaithful_decls=["adam:Tao.darboux"],
                        witness={"object": "a = b", "why": "the book admits it"}),
        ])
        self.findings = findings_mod.load_findings(
            [self.dir], source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA)
        self.cfg = compat.load_workspace(self.root)

    def test_adjudicate_args_carry_workspace_paths_and_bodies(self):
        payload = workflow_args.build_adjudicate_args(self.cfg, "adam", self.findings)
        # every absolute path in the args must be a workspace path or a repo
        # named in workspace.yaml -- never a session scratchpad path
        for path in _abs_paths(payload):
            self.assertTrue(path.startswith(self.root) or path.startswith("/repos/"),
                            "stray path in workflow args: %s" % path)
        # `sources` is a LIST: an adjudicator told only about "the book" cannot
        # know which of two editions a finding read.
        source = payload["workspace"]["sources"][0]
        self.assertEqual(source["source_id"], PUBLISHED_ID)
        self.assertEqual(source["source_sha"], OFFICIAL_SHA)
        self.assertEqual(source["page_offset"], 16)
        self.assertTrue(source["pages_dir"].startswith(self.root))
        self.assertTrue(source["text"].endswith(f"sources/{PUBLISHED_ID}/text.txt"))
        self.assertIn("standing hypothesis", payload["companion"]["conventions"])
        # the companion names its sources and the edition its anchors follow
        self.assertEqual(payload["companion"]["sources"], [PUBLISHED_ID])
        self.assertEqual(payload["companion"]["anchor_source"], PUBLISHED_ID)
        self.assertEqual([p["name"] for p in payload["peers"]], ["ours"])
        finding = payload["findings"][0]
        self.assertEqual(finding["finding_id"], self.findings[0]["finding_id"])
        self.assertEqual(finding["decls"][0]["line"], 2979)
        self.assertTrue(finding["decls"][0]["found_in_statements"])
        self.assertIn("ours", finding["peer_rows"])
        self.assertEqual(payload["model"], "opus")
        self.assertEqual(workflow_args.agent_estimate(payload), 7)

    def test_challenge_args_are_a_subset_with_refuters(self):
        payload = workflow_args.build_challenge_args(self.cfg, "adam", self.findings, refuters=2)
        self.assertEqual(payload["workflow"], "challenge")
        self.assertEqual(payload["refuters"], 2)
        self.assertEqual(workflow_args.agent_estimate(payload), 2)
        self.assertEqual(payload["findings"][0]["witness"]["object"], "a = b")

    def test_emit_args_through_the_cli(self):
        cmd = [sys.executable, os.path.join(SCRIPTS, "cf.py"), "adjudicate",
               "--workspace", self.root, "--companion", "adam", "--emit-args"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = os.path.join(self.root, "ledger", "args", "adjudicate.adam.json")
        self.assertTrue(os.path.exists(out), proc.stdout + proc.stderr)
        payload = compat.read_json(out)
        self.assertEqual(len(payload["findings"]), 1)
        self.assertIn("adjudicate.workflow.js", proc.stdout)
        self.assertIn("--transcribe", proc.stdout)


class TestHandoff(unittest.TestCase):
    """The handoff may contain only computed numbers, and must flag holes."""

    def setUp(self):
        self.root = scratch_dir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        compat.write_text(
            os.path.join(self.root, "workspace.yaml"),
            workspace_yaml(companions=(
                "  adam:\n    repo: /repos/mt\n    adapter: attribution_table\n"
                f"    sources: [{PUBLISHED_ID}]\n"
                f"    anchor_source: {PUBLISHED_ID}\n")))
        self.dir = write_corpus(self.root, "hunt", [
            finding_doc("Exercise 1.1.22", unfaithful_decls=["adam:Tao.darboux"], severity="high"),
        ])
        self.findings = findings_mod.load_findings(
            [self.dir], source_id=PUBLISHED_ID, source_sha=OFFICIAL_SHA)
        paths = compat.workspace_paths(self.root)
        route_mod.write_routed(
            paths, "adam",
            route_mod.route_repo(self.findings, "adam", source_id=PUBLISHED_ID,
                                source_sha=OFFICIAL_SHA, workspace=self.root))
        # The shape `cf tally` writes: grouped by (source_id, repo), with the
        # per-repo rollup naming its sources.
        block = {"rows": 1, "by_kind": {"CONTENT": 1},
                 "by_severity": {"low": 0, "medium": 0, "high": 1},
                 "raw": {"items": 1, "substantive": 1, "high": 1,
                         "classes": {"content_vac": 1, "suspected_errata": 0,
                                     "coverage": 0, "page_anchor": 0}},
                 "collapsed": {"items": 1, "substantive": 1, "high": 1, "classes": {}},
                 "per_run": {"hunt": {"rows": 1, "items": 1, "collapsed_items": 1}}}
        compat.write_json(os.path.join(self.root, "reports", "tally.hunt.json"), {
            "generated_at": "2026-09-16T00:00:00Z",
            "group_by": ["source_id", "repo"],
            "sources": {PUBLISHED_ID: OFFICIAL_SHA}, "rows": 1,
            "runs": {"hunt": {"dir": self.dir, "mode": "attack", "files": 1}},
            "groups": {"%s::adam" % PUBLISHED_ID: dict(block, source_id=PUBLISHED_ID,
                                                       repo="adam")},
            "repos": {"adam": dict(block, sources={PUBLISHED_ID: block})},
        })

    def test_handoff_reports_the_unchallenged_status_and_the_tally(self):
        report = handoff_mod.make_handoff(self.root, date="2026-09-16")
        text = report["text"]
        self.assertIn("findings only", text.lower())
        # A sources table, one row per source, naming the companions bound to it
        # -- the report may never say "the book".
        self.assertIn(PUBLISHED_ID, text)
        self.assertIn(DRAFT_ID, text)
        self.assertIn(OFFICIAL_SHA[:12], text)
        self.assertIn("AMS GSM 126 (published pagination)", text)
        self.assertIn("UNCHALLENGED", text)
        self.assertIn("`hunt`", text)
        # the findings tables are PER SOURCE: never one number over two editions
        self.assertNotIn("(unstamped)", text)
        self.assertIn("| `%s` | `hunt` | 1 |" % PUBLISHED_ID, text)
        self.assertIn("unchallenged row", text)
        # and the paths section names each source's own directories
        self.assertIn("sources/%s" % PUBLISHED_ID, text)
        self.assertIn("pairs/%s" % DRAFT_ID, text)
        self.assertIn("python3 scripts/cf.py route", text)
        self.assertTrue(os.path.exists(report["path"]))

    def test_one_run_gets_one_row_even_when_two_tallies_cover_it(self):
        """`cf tally` writes tally.all.json; harvest and detect-proxies write
        tally.<run>.json. Both cover the same run, and section 3 printed the
        run once per file -- the same numbers twice, for the reader to
        reconcile. A run is one run.
        """
        with open(os.path.join(self.root, "reports", "tally.hunt.json")) as fh:
            data = json.load(fh)
        compat.write_json(os.path.join(self.root, "reports", "tally.all.json"), data)
        text = handoff_mod.make_handoff(self.root, date="2026-09-16")["text"]
        row = "| `%s` | `hunt` | 1 |" % PUBLISHED_ID
        self.assertEqual(text.count(row), 1, "the same run was printed twice")
        # the per-TALLY totals table still shows both files: that one is about
        # the files, and it names them
        self.assertIn("tally.all.json", text)
        self.assertIn("tally.hunt.json", text)
        self.assertNotIn("Stale tally", text)

    def test_two_tallies_that_disagree_are_named_not_merged(self):
        """A disagreement means one of them is stale, and that is a fact."""
        with open(os.path.join(self.root, "reports", "tally.hunt.json")) as fh:
            data = json.load(fh)
        data["repos"]["adam"]["sources"][PUBLISHED_ID]["per_run"]["hunt"] = {
            "rows": 9, "items": 9, "collapsed_items": 9}
        compat.write_json(os.path.join(self.root, "reports", "tally.all.json"), data)
        text = handoff_mod.make_handoff(self.root, date="2026-09-16")["text"]
        self.assertIn("Stale tally", text)
        self.assertIn("tally.all.json", text)
        self.assertIn("cf tally", text)

    def test_missing_artifacts_become_visible_todos(self):
        report = handoff_mod.make_handoff(self.root, date="2026-09-16")
        self.assertTrue(report["todos"], "an empty workspace must not look complete")
        self.assertIn("TODO(what went wrong)", report["text"])

    def test_verify_claims_args_reference_only_workspace_sources(self):
        report = handoff_mod.make_handoff(self.root, date="2026-09-16")
        ctx = report["context"]
        payload = claims_mod.build_args(
            self.root, draft=report["text"], companions=["adam"],
            workspace_cfg=ctx["_workspace"],
            verification_commands=ctx["verification_commands"])
        keys = [l["key"] for l in payload["lenses"]]
        self.assertIn("corpus-numbers", keys)
        self.assertIn("ledger-and-routing", keys)
        self.assertTrue(payload["numbers"]["enabled"])
        self.assertTrue(payload["numbers"]["commands"])
        self.assertTrue(all(s["path"].startswith(self.root) or os.path.isabs(s["path"])
                            for s in payload["sources"]))
        self.assertEqual(payload["model"], "opus")


class TestWorkflowScripts(unittest.TestCase):
    """The Workflow scripts must parse and must not smuggle in session paths."""

    WORKFLOWS = ("adjudicate", "challenge", "verify-claims", "fleet-local")

    def paths(self):
        return [os.path.join(SKILL_ROOT, "workflows", "%s.workflow.js" % n) for n in self.WORKFLOWS]

    def test_all_four_exist(self):
        for path in self.paths():
            self.assertTrue(os.path.exists(path), path)

    @staticmethod
    def _read(path: str) -> str:
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_no_session_or_machine_paths(self):
        for path in self.paths():
            text = self._read(path)
            for needle in ("scratchpad/", "/private/tmp", "/Users/", "/home/"):
                self.assertNotIn(needle, text, "%s leaks %s" % (os.path.basename(path), needle))

    def test_meta_is_a_literal_with_name_and_description(self):
        for path in self.paths():
            text = self._read(path)
            self.assertTrue(text.startswith("export const meta = {"), os.path.basename(path))
            head = text.split("}\n", 1)[0]
            self.assertIn("name:", head)
            self.assertIn("description:", head)
            self.assertNotIn("${", head, "meta must be a pure literal")

    def test_adjudication_and_challenge_agents_are_pinned_to_opus(self):
        # the assignment pins every adjudication/challenge agent to opus; the
        # local fleet's worker inherits the session model on purpose, so only
        # its challenge stage is pinned.
        for name in ("adjudicate", "challenge"):
            text = self._read(os.path.join(SKILL_ROOT, "workflows", "%s.workflow.js" % name))
            self.assertIn("A.model || 'opus'", text)
            self.assertEqual(text.count("model: MODEL"), text.count("agent("),
                             "%s: every agent() must carry model: MODEL" % name)
        fleet = self._read(os.path.join(SKILL_ROOT, "workflows", "fleet-local.workflow.js"))
        self.assertIn("A.challenge_model || 'opus'", fleet)
        self.assertIn("model: CHALLENGE_MODEL", fleet)

    def test_no_forbidden_runtime_calls(self):
        for path in self.paths():
            text = self._read(path)
            for needle in ("Date.now(", "Math.random(", "require(", "fs."):
                self.assertNotIn(needle, text, "%s uses %s" % (os.path.basename(path), needle))

    def test_scripts_parse(self):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node is not installed")
        tmp = scratch_dir()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for path in self.paths():
            body = self._read(path).replace("export const meta =", "const meta =", 1)
            wrapped = os.path.join(tmp, os.path.basename(path))
            with open(wrapped, "w", encoding="utf-8") as fh:
                fh.write("(async function(){\n%s\n})\n" % body)  # the runtime wraps the body
            proc = subprocess.run([node, "--check", wrapped], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, "%s: %s" % (os.path.basename(path), proc.stderr))


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
