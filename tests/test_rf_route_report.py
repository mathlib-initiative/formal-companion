"""Review fixes for the route and report stages (stdlib unittest).

Three findings, all reproduced before they were fixed:

* critic#1 -- the pipeline's terminal deliverable carried no evidence.  The owner
  message was a five-column table (item, kind, severity, declaration, verdict),
  the routed row held no page and no ``file:line``, and nothing filed a finding
  anywhere.  It now renders one section per finding with the printed page, the
  book sentence under a per-DESTINATION quote policy, the declaration at
  ``file:line`` with its statement head, the difference, the witness, the
  adjudication outcome and the finding id -- and ``cf route --github`` drafts one
  issue body per accepted finding plus the exact ``gh issue create`` command,
  without ever calling ``gh`` or the network.  Its second half is the collision
  hand-off wave 1 left open: two findings sharing one ``finding_id`` were both
  handed the single ledger row addressed to it and both marked CHALLENGED.
* critic#5 -- the handoff had no denominator.  It now carries, per source and
  chapter, items in the source / in scope / in the differential's task universe /
  paired / examined / gated / with findings / never examined, with the unexamined
  ones written out by name, and a first paragraph that says outright when the
  scope is a part of a source -- and does NOT say so merely because the workspace
  holds a reference edition nobody is bound to.
* first-run#6 -- the route hint printed ``python3 scripts/cf.py adjudicate
  --workspace <workspace> ...``: a path relative to the skill checkout and a
  literal placeholder, and it was written into the routed file and into the
  owner-message draft, not merely printed.

The synthetic book, companion and findings below are invented: no source text and
no companion's Lean live in this repo (CLAUDE.md S1).  Every test here runs
without the private baselines.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

from ledger import compat  # noqa: E402
from ledger import findings as findings_mod  # noqa: E402
from ledger import validate_ledger as vl  # noqa: E402
from ledger import workflow_args  # noqa: E402
from report import make_handoff as handoff_mod  # noqa: E402
from route import route as route_mod  # noqa: E402

TMPDIR = os.environ.get("CF_TMPDIR") or None

SOURCE_ID = "a-book-1st-edition"
SOURCE_SHA = "a" * 64
OTHER_SOURCE_ID = "a-book-2nd-edition"
OTHER_SOURCE_SHA = "b" * 64
COMPANION = "ours"
#: the sentence a finding quotes; invented, not a line of any real book
QUOTE = ("Let E be an elementary set. Then the outer measure of every subset of E "
         "is at most the outer measure of E itself.")


def scratch() -> str:
    """A throwaway directory; realpath because cflib resolves the paths it builds."""
    return os.path.realpath(tempfile.mkdtemp(prefix="cf-rf-route-", dir=TMPDIR))


def workspace_yaml(*, repo: str = "", policy: str = "", chapters: str = "") -> str:
    """A v2 workspace.yaml holding two sources and one companion.

    ``repo`` must be a real directory: ``cflib.workspace`` validates it, and a
    workspace it refuses falls back to the lenient reader, which never walks up
    to an ancestor -- so an invalid fixture would hide the resolved-workspace
    behaviour this file is about.
    """
    return (
        "version: 2\n"
        "workspace:\n  name: rf\n"
        "companions:\n"
        "  %s:\n"
        "    repo: %s\n"
        "    adapter: attribution_table\n"
        "    owner: examplebox/%s\n"
        "    sources: [%s]\n"
        "    anchor_source: %s\n"
        "sources:\n"
        "  %s:\n"
        '    sha256: "%s"\n'
        "    title: A Book\n"
        '    edition: "first edition"\n'
        "    offset: 16\n"
        "%s"
        "    role: audited\n"
        "  %s:\n"
        '    sha256: "%s"\n'
        "    title: A Book\n"
        '    edition: "second edition"\n'
        "    offset: 16\n"
        "    role: reference\n"
        "hosts:\n  allowlist: []\n"
        "%s"
        % (COMPANION, repo or "/repos/%s" % COMPANION, COMPANION, SOURCE_ID, SOURCE_ID,
           SOURCE_ID, SOURCE_SHA, chapters, OTHER_SOURCE_ID, OTHER_SOURCE_SHA, policy)
    )


def finding(item: str, *, decls, kind="CONTENT", severity="low", concept="the concept",
            quote=QUOTE, source_id=SOURCE_ID, source_sha=SOURCE_SHA, **extra) -> dict:
    """One corpus document carrying a single disagreement."""
    row = {
        "kind": kind,
        "severity": severity,
        "concept": concept,
        "unfaithful_decls": list(decls),
        "repos_involved": [COMPANION],
        "faithful": [],
        "reasoning": "the Lean statement drops the measurability hypothesis",
        "suggested_fix": "add the hypothesis",
        "book_quote": quote,
    }
    row.update(extra)
    return {"item": item, "book_located": True, "source_id": source_id,
            "source_sha": source_sha, "matches": [], "coverage_notes": "",
            "disagreements": [row]}


def write_corpus(root: str, run_id: str, docs, *, gate_ok: bool | None = True) -> str:
    """A corpus run directory, with a RECEIPT whose gate block says what it says."""
    directory = os.path.join(root, "corpus", run_id)
    for n, doc in enumerate(docs):
        slug = re.sub(r"[^a-z0-9]+", "-", str(doc["item"]).lower()).strip("-") or "doc%d" % n
        compat.write_json(os.path.join(directory, "%s.json" % slug), doc)
    receipt = {"run_id": run_id, "sources": {SOURCE_ID: SOURCE_SHA},
               "actual": {"count": len(docs)}, "complete": True}
    if gate_ok is not None:
        receipt["gate"] = {"ok": bool(gate_ok), "checked": len(docs)}
    compat.write_json(os.path.join(directory, "RECEIPT.json"), receipt)
    return directory


def write_statements(root: str, rows) -> str:
    """companions/<name>/statements.jsonl, the file:line the deliverable needs."""
    path = os.path.join(root, "companions", COMPANION, "statements.jsonl")
    compat.write_jsonl(path, rows)
    return path


def statement_row(decl: str, *, file: str, line: int, statement: str,
                  item: str = "") -> dict:
    return {"item": item, "item_raw": item, "decl": decl, "file": file, "line": line,
            "statement": statement, "repo": COMPANION, "source_id": SOURCE_ID,
            "source_sha": SOURCE_SHA}


def write_items_json(root: str, source_id: str, rows) -> str:
    """sources/<id>/items.json: the item universe and each item's printed page."""
    path = os.path.join(root, "sources", source_id, "items.json")
    compat.write_json(path, {"source_id": source_id, "sha256": SOURCE_SHA,
                             "count": len(rows), "items": list(rows)})
    return path


def write_source_text(root: str, source_id: str = SOURCE_ID) -> str:
    """sources/<id>/text.txt: the pinned text a draft's prose is checked against.

    Invented, like everything else here.  A workspace without it is a workspace
    where `cf route` cannot tell a worker's own sentence from the book's, which is
    now a refusal for every destination but the owner's -- so the fixture pins it.
    """
    path = os.path.join(root, "sources", source_id, "text.txt")
    compat.write_text(path, "\f".join([
        "Chapter 1. Elementary measure\n\nWe begin with the elementary theory.\n",
        f"Exercise 1.2.3. {QUOTE}\n",
        "Lemma 1.4.1. Every elementary set has an outer measure.\n",
    ]))
    return path


def item_row(item: str, *, page: int, chapter: int) -> dict:
    return {"item": item, "page": page, "pdf_page": page + 16, "chapter": chapter,
            "kind": item.split(" ")[0], "line": 1}


def write_reconciliation(root: str, source_id: str, **body) -> str:
    path = os.path.join(root, "pairs", source_id, "reconciliation.json")
    compat.write_json(path, dict({"source_id": source_id, "source_sha": SOURCE_SHA}, **body))
    return path


def write_pair_index(root: str, source_id: str, items) -> str:
    """pairs/<id>/index.json: which companions formalise each item.

    ``items`` is ``{item: [companion, ...]}``; two or more companions on one item
    is what "paired" means, and this file is the only artifact that says it.
    """
    path = os.path.join(root, "pairs", source_id, "index.json")
    compat.write_json(path, {
        "source_id": source_id, "source_sha": SOURCE_SHA,
        "repos": sorted({r for repos in items.values() for r in repos}),
        "items": {item: {repo: ["%s:Book.decl" % repo] for repo in repos}
                  for item, repos in items.items()},
        "intext": {},
    })
    return path


def ledger_row(fid: str, item: str, *, verdict: str, **extra) -> dict:
    row = {"finding_id": fid, "item": item, "repo": COMPANION, "source_id": SOURCE_ID,
           "source_sha": SOURCE_SHA, "kind": "CONTENT", "verdict": verdict,
           "method": "workflow:adjudicate", "severity": "high", "contested": False,
           "dissent": [], "schema": "ledger-v2"}
    row.update(extra)
    return row


def write_ledger(root: str, rows, *, refuted=()) -> None:
    compat.write_jsonl(os.path.join(root, "ledger", "accepted.%s.jsonl" % COMPANION), list(rows))
    if refuted:
        compat.write_jsonl(os.path.join(root, "ledger", "refuted.%s.jsonl" % COMPANION),
                           list(refuted))


def cf(*argv, cwd=None):
    """Run the CLI the way a user does, from *cwd*. Never touches the network."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(SKILL / "scripts" / "cf.py"), *argv],
                          capture_output=True, text=True, cwd=cwd, env=env)


class Fixture(unittest.TestCase):
    """A workspace with a source, a companion, a statement table and one run."""

    POLICY = ""
    CHAPTERS = ""
    GATE_OK: bool | None = True

    def setUp(self):
        self.root = scratch()
        self.addCleanup(_rmtree, self.root)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo, exist_ok=True)
        compat.write_text(os.path.join(self.root, "workspace.yaml"),
                          workspace_yaml(repo=self.repo, policy=self.POLICY,
                                         chapters=self.CHAPTERS))
        write_items_json(self.root, SOURCE_ID, [
            item_row("Exercise 1.2.3", page=24, chapter=1),
            item_row("Lemma 1.4.1", page=31, chapter=1),
            item_row("Exercise 2.1.1", page=58, chapter=2),
        ])
        write_source_text(self.root)
        write_statements(self.root, [
            statement_row("Book.outerMeasure_mono", file="Book/Outer.lean", line=112,
                          item="Exercise 1.2.3",
                          statement="theorem outerMeasure_mono (E F : Set ℝ) : μ F ≤ μ E"),
        ])
        self.docs = [
            # The reasoning and the witness QUOTE the book, which is what a worker
            # explaining a difference does: the excerpt policy governs `book_quote`
            # alone, so a draft for a public tracker used to carry the sentence in
            # these two fields whatever the policy said.
            finding("Exercise 1.2.3", decls=["%s:Book.outerMeasure_mono" % COMPANION],
                    severity="high",
                    witness={"counterexample": "E = ∅", "why": QUOTE},
                    reasoning="the Lean statement drops the measurability hypothesis, "
                              "where the book says: %s" % QUOTE),
            finding("Lemma 1.4.1", decls=["%s:Book.missing" % COMPANION]),
        ]
        write_corpus(self.root, "hunt", self.docs, gate_ok=self.GATE_OK)
        self.paths = compat.workspace_paths(self.root)

    def route(self, **kwargs) -> dict:
        return route_mod.route_workspace(self.root, **kwargs)[0]

    def finding_ids(self) -> dict:
        rows = findings_mod.load_findings([os.path.join(self.root, "corpus", "hunt")])
        return {r["item"]: r["finding_id"] for r in rows}


def _rmtree(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# critic#1 -- the routed row carries its evidence
# ---------------------------------------------------------------------------

class TestTheRoutedRowCarriesItsEvidence(Fixture):
    def test_the_row_carries_the_printed_page_and_the_declaration_site(self):
        rows = {r["item"]: r for r in self.route()["routed"]["rows"]}
        row = rows["Exercise 1.2.3"]
        self.assertEqual(row["page"], 24, "the printed page comes from items.json")
        site = row["decl_sites"][0]
        self.assertEqual(site["decl"], "%s:Book.outerMeasure_mono" % COMPANION)
        self.assertEqual((site["file"], site["line"]), ("Book/Outer.lean", 112))
        self.assertIn("outerMeasure_mono", site["statement"])
        self.assertTrue(site["found"])

    def test_a_declaration_the_statement_table_lacks_is_said_so_not_dropped(self):
        rows = {r["item"]: r for r in self.route()["routed"]["rows"]}
        site = rows["Lemma 1.4.1"]["decl_sites"][0]
        self.assertFalse(site["found"])
        self.assertNotIn("file", site)

    def test_the_evidence_comes_from_the_one_statements_reader(self):
        """The adjudication lenses and the deliverable read the same index.

        A second reader of statements.jsonl would be a second chance for the
        ``file:line`` in the owner's message to disagree with the one the lens
        that produced the verdict was shown.
        """
        index = workflow_args.load_statements(self.paths, COMPANION)
        sites = route_mod.decl_sites(index, ["%s:Book.outerMeasure_mono" % COMPANION])
        rows = {r["item"]: r for r in self.route()["routed"]["rows"]}
        self.assertEqual(rows["Exercise 1.2.3"]["decl_sites"], sites)

    def test_a_caller_that_passes_no_evidence_gets_the_rows_it_always_got(self):
        """route_repo's evidence arguments are optional: no page, no decl_sites."""
        rows = findings_mod.load_findings([os.path.join(self.root, "corpus", "hunt")])
        routed = route_mod.route_repo(rows, COMPANION, source_id=SOURCE_ID,
                                      source_sha=SOURCE_SHA, workspace=self.root)
        for row in routed["rows"]:
            self.assertNotIn("page", row)
            self.assertNotIn("decl_sites", row)


class TestTheOwnerMessageIsTheDeliverable(Fixture):
    def message(self, **kwargs) -> str:
        self.route(**kwargs)
        with open(os.path.join(self.root, "ledger", "routed",
                               "message.%s.md" % COMPANION), encoding="utf-8") as fh:
            return fh.read()

    def test_every_routed_finding_renders_its_evidence(self):
        text = self.message()
        self.assertIn("## Exercise 1.2.3 — CONTENT, high", text)
        self.assertIn("printed page 24", text)
        self.assertIn(QUOTE, text, "the owner owns the book: the sentence is there")
        self.assertIn("`Book/Outer.lean:112`", text)
        self.assertIn("outerMeasure_mono (E F : Set ℝ)", text)
        self.assertIn("the Lean statement drops the measurability hypothesis", text)
        self.assertIn("counterexample=E = ∅", text)
        self.assertIn("add the hypothesis", text)
        self.assertIn(self.finding_ids()["Exercise 1.2.3"], text)

    def test_it_is_no_longer_a_five_column_table(self):
        """The old deliverable was exactly this header and nothing else."""
        text = self.message()
        self.assertNotIn("| item | kind | severity | declaration | verdict |", text)
        self.assertIn("| item | kind | severity | page | declaration | verdict |", text)

    def test_an_unchallenged_row_says_it_may_not_be_reported(self):
        text = self.message()
        self.assertIn("must not be sent", text)
        self.assertIn("not challenged yet", text)

    def test_the_verdict_line_names_who_challenged_it(self):
        ids = self.finding_ids()
        write_ledger(self.root, [
            ledger_row(ids["Exercise 1.2.3"], "Exercise 1.2.3", verdict="CONFIRMED_CONTENT",
                       contested=True,
                       dissent=[{"role": "convention-skeptic", "confidence": 0.8,
                                 "proposed_verdict": "CONVENTION",
                                 "argument": "the house convention covers it"}]),
        ])
        text = self.message()
        self.assertIn("**CONFIRMED_CONTENT** (workflow:adjudicate)", text)
        self.assertIn("convention-skeptic proposed CONVENTION at 0.8", text)
        self.assertIn("**contested**", text)


class TestQuotePolicyIsPerDestination(Fixture):
    def test_a_public_destination_keeps_the_page_the_ends_and_the_hash(self):
        rendered = route_mod.render_quote(QUOTE, route_mod.QUOTE_EXCERPT, page=24)
        self.assertNotIn(QUOTE, rendered)
        self.assertIn(" ".join(QUOTE.split()[:6]), rendered)
        self.assertIn(" ".join(QUOTE.split()[-6:]), rendered)
        self.assertIn("printed page 24", rendered)
        self.assertIn(compat.sha256_text(" ".join(QUOTE.split()).lower())[:12], rendered)

    def test_the_owner_destination_keeps_the_sentence(self):
        self.assertIn(QUOTE, route_mod.render_quote(QUOTE, route_mod.QUOTE_FULL, page=24))

    def test_a_full_quote_stops_at_the_gates_own_bound(self):
        long_quote = "word " * 400
        rendered = route_mod.render_quote(long_quote, route_mod.QUOTE_FULL)
        self.assertLessEqual(len(rendered), route_mod.LONG_QUOTE_CHARS + 80)
        self.assertIn("truncated at %d characters" % route_mod.LONG_QUOTE_CHARS, rendered)

    def test_the_bound_is_spelled_like_the_gates_own_bound(self):
        """``MAX_QUOTE_CHARS`` is the gate's 3,000-character REFUSAL, not this one."""
        from gate import verify_findings as vf

        self.assertEqual(route_mod.LONG_QUOTE_CHARS, vf.LONG_QUOTE_CHARS)
        self.assertFalse(hasattr(route_mod, "MAX_QUOTE_CHARS"),
                         "two modules must not disagree about what one name bounds")

    def test_the_defaults_are_inverted_per_destination_not_per_workspace(self):
        self.assertEqual(route_mod.quote_policy({}, "owner"), route_mod.QUOTE_FULL)
        self.assertEqual(route_mod.quote_policy({}, "public"), route_mod.QUOTE_EXCERPT)

    def test_the_workspace_may_override_a_destination(self):
        policy = {"destinations": {"owner": {"quotes": "excerpt"}}}
        self.assertEqual(route_mod.quote_policy(policy, "owner"), route_mod.QUOTE_EXCERPT)

    def test_an_unknown_destination_is_refused_and_names_the_flag(self):
        with self.assertRaises(route_mod.PolicyRefused) as ctx:
            route_mod.quote_policy({}, "twitter")
        self.assertIn("--destination", str(ctx.exception))

    def test_an_illegal_policy_value_is_refused(self):
        with self.assertRaises(route_mod.PolicyRefused) as ctx:
            route_mod.quote_policy({"destinations": {"public": {"quotes": "verbatim"}}}, "public")
        self.assertIn("--quotes", str(ctx.exception))

    def test_a_public_owner_message_does_not_carry_the_sentence(self):
        self.route(destination="public")
        with open(os.path.join(self.root, "ledger", "routed",
                               "message.%s.md" % COMPANION), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn(QUOTE, text)
        self.assertIn("Quotes: excerpt (destination `public`)", text)


class ChallengedFixture(Fixture):
    """The same workspace with every routed row adjudicated, so exports are allowed."""

    def setUp(self):
        super().setUp()
        # a second accepted finding on ONE item, to exercise the grouping rule
        extra = finding("Exercise 1.2.3", decls=["%s:Book.outerMeasure_mono" % COMPANION],
                        kind="VACUITY", severity="medium", concept="a second claim")
        compat.write_json(os.path.join(self.root, "corpus", "hunt", "exercise-1-2-3-b.json"),
                          extra)
        ids = {r["finding_id"]: r for r in findings_mod.load_findings(
            [os.path.join(self.root, "corpus", "hunt")])}
        rows = []
        for fid, row in ids.items():
            verdict = "CONFIRMED_CONTENT" if row["kind"] != "VACUITY" else "ATTRIBUTION_GAP"
            if row["item"] == "Lemma 1.4.1":
                verdict = "CONVENTION"          # challenged, but not reportable
            rows.append(ledger_row(fid, row["item"], verdict=verdict,
                                   kind=row["kind"], recovery_argument="the house convention"
                                   if verdict == "CONVENTION" else ""))
        write_ledger(self.root, rows)
        self.ledger_rows = rows


class TestGithubIssueBodies(ChallengedFixture):
    def test_the_file_is_challenged_first(self):
        rep = self.route()
        self.assertEqual(rep["routed"]["meta"]["status"], route_mod.STATUS_CHALLENGED)

    def test_one_body_per_item_with_an_index_and_a_gh_command(self):
        rep = self.route()
        out = os.path.join(self.root, "issues")
        index = route_mod.write_github_issues(
            rep["routed"], out, companion=COMPANION, repo="examplebox/ours",
            workspace=self.root, routed_file=rep["file"])
        self.assertEqual(index["count"], 1, "the CONVENTION row is not reportable")
        self.assertEqual(index["findings"], 2, "two accepted findings share one item")
        body = os.path.join(out, index["issues"][0]["file"])
        self.assertTrue(os.path.exists(body))
        self.assertTrue(os.path.exists(os.path.join(out, "index.json")))
        command = index["commands"][0]
        self.assertIn("gh issue create", command)
        self.assertIn("--repo examplebox/ours", command)
        self.assertIn("--body-file %s" % os.path.abspath(body), command)
        self.assertIn("--label faithfulness", command)

    def test_the_body_carries_the_evidence_under_the_public_policy(self):
        rep = self.route()
        built = route_mod.github_issues(rep["routed"], companion=COMPANION,
                                       repo="examplebox/ours", workspace=self.root,
                                       routed_file=rep["file"])
        body = built["issues"][0]["body"]
        self.assertIn("printed page 24", body)
        self.assertIn("`Book/Outer.lean:112`", body)
        self.assertIn("CONFIRMED_CONTENT", body)
        self.assertIn(" ".join(QUOTE.split()[:6]), body)
        self.assertNotIn(QUOTE, body, "a public tracker gets the excerpt, not the sentence")
        self.assertIn("cf route", body, "the body says how to reproduce itself")

    def test_a_title_and_labels_are_suggested_never_filed(self):
        rep = self.route()
        index = route_mod.github_issues(rep["routed"], companion=COMPANION,
                                       workspace=self.root)["index"]
        issue = index["issues"][0]
        self.assertTrue(issue["title"].startswith("Exercise 1.2.3:"))
        self.assertIn("faithfulness", issue["labels"])
        self.assertIn("kind:CONTENT", issue["labels"])
        self.assertIn("severity:high", issue["labels"])
        self.assertIn("never run", index["gh_note"])

    def test_without_a_repo_the_command_still_runs_and_the_note_says_how(self):
        rep = self.route()
        index = route_mod.github_issues(rep["routed"], companion=COMPANION,
                                       workspace=self.root)["index"]
        self.assertEqual(index["repo"], "")
        self.assertIn("--repo owner/name", index["gh_note"])

    def test_the_body_carries_no_absolute_path_of_the_auditors_machine(self):
        """It is drafted for a tracker anyone can read; the layout is not a finding."""
        rep = self.route()
        built = route_mod.github_issues(rep["routed"], companion=COMPANION,
                                       workspace=self.root, routed_file=rep["file"])
        body = built["issues"][0]["body"]
        self.assertNotIn(self.root, body)
        self.assertNotIn(os.sep + "private", body)
        self.assertIn("ledger/routed/findings.%s.jsonl" % COMPANION, body)

    def test_the_cli_writes_the_bodies_and_prints_the_command(self):
        res = cf("route", "--github", cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = route_mod.issues_dir(os.path.join(self.root, "ledger", "routed"), COMPANION)
        with open(os.path.join(out, "index.json"), encoding="utf-8") as fh:
            index = json.load(fh)
        self.assertEqual(index["count"], 1)
        self.assertEqual(index["quotes"], route_mod.QUOTE_EXCERPT,
                         "--github addresses a public tracker by default")
        self.assertEqual(index["repo"], "examplebox/%s" % COMPANION,
                         "the companion's own `owner` slug, never a git remote")
        self.assertIn("gh issue create", res.stdout)
        self.assertIn("nothing was filed", res.stdout)
        self.assertTrue(os.path.exists(os.path.join(out, index["issues"][0]["file"])))

    def out_dir(self, name: str = COMPANION) -> str:
        """``--out DIR`` writes into ``DIR/<companion>/``, never into DIR itself."""
        return os.path.join(self.root, "issues", name)

    def test_an_explicit_destination_governs_both_drafts(self):
        """`--destination owner` means owner everywhere, not "the default"."""
        res = cf("route", "--github", "--destination", "owner", "--out",
                 os.path.join(self.root, "issues"), cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(os.path.join(self.out_dir(), "index.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["quotes"], route_mod.QUOTE_FULL)

    def test_the_cli_honours_an_explicit_quote_policy(self):
        res = cf("route", "--github", "--quotes", "hash", "--out",
                 os.path.join(self.root, "issues"), cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        body = pathlib.Path(self.out_dir()).glob("*.md")
        text = next(iter(sorted(body))).read_text(encoding="utf-8")
        self.assertIn("quote withheld for this destination", text)
        self.assertNotIn(QUOTE, text)

    def test_two_item_labels_that_slug_alike_do_not_overwrite_one_body(self):
        """Measured 0 times over the 347 baseline findings; silent when it happens."""
        rep = self.route()
        rows = [r for r in rep["routed"]["rows"]
                if str(r.get("ledger_verdict") or "") in route_mod.REPORTABLE_VERDICTS]
        self.assertTrue(rows)
        twin = dict(rows[0], item=rows[0]["item"] + ".", finding_id="f" * 64)
        routed = {"meta": rep["routed"]["meta"], "rows": rep["routed"]["rows"] + [twin]}
        self.assertEqual(route_mod.issue_slug(twin["source_id"], twin["item"]),
                         route_mod.issue_slug(rows[0]["source_id"], rows[0]["item"]),
                         "the fixture must really collide on the file name")
        out = os.path.join(self.root, "twins")
        index = route_mod.write_github_issues(routed, out, companion=COMPANION,
                                             workspace=self.root)
        names = [i["file"] for i in index["issues"]]
        self.assertEqual(len(names), len(set(names)), "index.json listed one name twice")
        for name in names:
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)
        self.assertEqual(len([f for f in os.listdir(out) if f.endswith(".md")]), len(names))

    def test_the_workspace_policy_reaches_the_issue_bodies(self):
        """``policy.destinations.public.quotes`` governs the artifact it names."""
        rep = self.route()
        built = route_mod.github_issues(
            rep["routed"], companion=COMPANION, workspace=self.root,
            policy={"destinations": {"public": {"quotes": route_mod.QUOTE_HASH}}})
        self.assertEqual(built["index"]["quotes"], route_mod.QUOTE_HASH)
        self.assertIn("quote withheld for this destination", built["issues"][0]["body"])

    def test_only_reportable_verdicts_become_bodies(self):
        rep = self.route()
        built = route_mod.github_issues(rep["routed"], companion=COMPANION,
                                       workspace=self.root)
        items = {i["item"] for i in built["issues"]}
        self.assertEqual(items, {"Exercise 1.2.3"})
        self.assertNotIn("Lemma 1.4.1", items)


class TwoCompanionFixture(unittest.TestCase):
    """The project's NORMAL shape: two companions, both formalising one item.

    The single-companion fixture above cannot see the destination-sharing defect
    at all, which is why it survived review: with one report there is nothing for
    ``--out`` to collide with.
    """

    NAMES = ("ours", "adam")
    ITEM = "Exercise 1.2.3"

    def setUp(self):
        self.root = scratch()
        self.addCleanup(_rmtree, self.root)
        lines = ["version: 2", "workspace:", "  name: rf", "companions:"]
        for name in self.NAMES:
            repo = os.path.join(self.root, "repo-%s" % name)
            os.makedirs(repo, exist_ok=True)
            lines += ["  %s:" % name, "    repo: %s" % repo,
                      "    adapter: attribution_table", "    owner: examplebox/%s" % name,
                      "    sources: [%s]" % SOURCE_ID, "    anchor_source: %s" % SOURCE_ID]
        lines += ["sources:", "  %s:" % SOURCE_ID, '    sha256: "%s"' % SOURCE_SHA,
                  "    title: A Book", '    edition: "first edition"', "    offset: 16",
                  "    role: audited", "hosts:", "  allowlist: []"]
        compat.write_text(os.path.join(self.root, "workspace.yaml"), "\n".join(lines) + "\n")
        write_items_json(self.root, SOURCE_ID, [item_row(self.ITEM, page=24, chapter=1)])
        write_source_text(self.root)
        docs = []
        for name in self.NAMES:
            decl = "Book.m_%s" % name
            compat.write_jsonl(
                os.path.join(self.root, "companions", name, "statements.jsonl"),
                [{"item": self.ITEM, "item_raw": self.ITEM, "decl": decl,
                  "file": "Book/%s.lean" % name, "line": 112, "repo": name,
                  "statement": "theorem %s : True" % decl, "source_id": SOURCE_ID,
                  "source_sha": SOURCE_SHA}])
            doc = finding(self.ITEM, decls=["%s:%s" % (name, decl)], severity="high")
            doc["disagreements"][0]["repos_involved"] = [name]
            compat.write_json(
                os.path.join(self.root, "corpus", "hunt", "exercise-1-2-3-%s.json" % name), doc)
            docs.append(doc)
        compat.write_json(os.path.join(self.root, "corpus", "hunt", "RECEIPT.json"),
                          {"run_id": "hunt", "sources": {SOURCE_ID: SOURCE_SHA},
                           "actual": {"count": len(docs)}, "complete": True,
                           "gate": {"ok": True, "checked": len(docs)}})
        rows = findings_mod.load_findings([os.path.join(self.root, "corpus", "hunt")])
        for name in self.NAMES:
            mine = findings_mod.rows_naming(rows, name)
            compat.write_jsonl(
                os.path.join(self.root, "ledger", "accepted.%s.jsonl" % name),
                [dict(ledger_row(r["finding_id"], r["item"], verdict="CONFIRMED_CONTENT"),
                      repo=name) for r in mine])


class TestTwoCompanionsNeverShareAnIssueDirectory(TwoCompanionFixture):
    """The printed `gh` command must name ITS OWN companion's evidence.

    With one ``--out`` for every report, ``index.json`` held only the last
    companion and the body for an item both companions formalise was overwritten,
    so pasting the first printed command opened the SECOND companion's claim on
    the first one's public tracker -- the exact harm this whole path exists to
    avoid, and it warned about nothing.
    """

    def test_out_gives_each_companion_its_own_index_and_body(self):
        out = os.path.join(self.root, "issues")
        res = cf("route", "--github", "--out", out, cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        for name in self.NAMES:
            with self.subTest(companion=name):
                index_path = os.path.join(out, name, "index.json")
                self.assertTrue(os.path.exists(index_path), index_path)
                with open(index_path, encoding="utf-8") as fh:
                    index = json.load(fh)
                self.assertEqual(index["companion"], name)
                self.assertEqual(index["count"], 1)
                body = os.path.join(out, name, index["issues"][0]["file"])
                text = pathlib.Path(body).read_text(encoding="utf-8")
                self.assertIn("Book.m_%s" % name, text)
                for other in self.NAMES:
                    if other != name:
                        self.assertNotIn("Book.m_%s" % other, text)

    def test_each_printed_body_file_is_a_distinct_path(self):
        out = os.path.join(self.root, "issues")
        res = cf("route", "--github", "--out", out, cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        printed = re.findall(r"--body-file (\S+)", res.stdout)
        self.assertEqual(len(printed), len(self.NAMES), res.stdout)
        self.assertEqual(len(set(printed)), len(printed),
                         "two companions were told to file the same file")
        for path, name in zip(sorted(printed), sorted(self.NAMES)):
            self.assertIn("Book.m_%s" % name,
                          pathlib.Path(path).read_text(encoding="utf-8"))

    def test_the_default_destination_was_already_per_companion(self):
        """No --out: the behaviour that was right stays right."""
        res = cf("route", "--github", cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        routed_dir = os.path.join(self.root, "ledger", "routed")
        for name in self.NAMES:
            self.assertTrue(os.path.exists(
                os.path.join(route_mod.issues_dir(routed_dir, name), "index.json")))


class TestTheWorkspacePolicyGovernsTheGithubBodies(ChallengedFixture):
    """`templates/workspace.yaml.example` annotates `public:` as the issue bodies.

    The key was read for the owner message and ignored by the artifact it names,
    in the PERMISSIVE direction: a workspace asking for `hash` published the
    excerpt on a tracker anyone can read.
    """

    POLICY = ("policy:\n"
              "  destinations:\n"
              "    owner:\n"
              "      quotes: hash\n"
              "    public:\n"
              "      quotes: hash\n")

    def test_the_cli_honours_destinations_public_quotes(self):
        res = cf("route", "--github", cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = route_mod.issues_dir(os.path.join(self.root, "ledger", "routed"), COMPANION)
        with open(os.path.join(out, "index.json"), encoding="utf-8") as fh:
            index = json.load(fh)
        self.assertEqual(index["quotes"], route_mod.QUOTE_HASH)
        body = pathlib.Path(out, index["issues"][0]["file"]).read_text(encoding="utf-8")
        self.assertIn("quote withheld for this destination", body)
        self.assertNotIn(QUOTE, body)
        self.assertNotIn(" ".join(QUOTE.split()[:6]), body)

    def test_the_report_dict_carries_the_block_the_emitter_needs(self):
        self.assertEqual((self.route().get("policy") or {}).get("destinations", {})
                         .get("public", {}).get("quotes"), route_mod.QUOTE_HASH)


class TestGithubIsRefusedWhileUnchallenged(Fixture):
    def test_unchallenged_rows_produce_no_issue_body(self):
        rep = self.route()
        out = os.path.join(self.root, "issues")
        with self.assertRaises(route_mod.UpstreamRefused) as ctx:
            route_mod.write_github_issues(rep["routed"], out, companion=COMPANION,
                                         workspace=self.root)
        self.assertIn("UNCHALLENGED", str(ctx.exception))
        self.assertIn("--challenge-only", str(ctx.exception))
        self.assertFalse(os.path.exists(out), "nothing may be written on a refusal")

    def test_the_cli_refuses_with_exit_code_two(self):
        res = cf("route", "--github", "--repo", "examplebox/ours", cwd=self.root)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("UNCHALLENGED", res.stderr)


class TestNothingInTheGithubPathCallsGh(unittest.TestCase):
    """`cf route --github` prints a command; opening a thread is a human's decision."""

    FILES = ("scripts/route/route.py", "scripts/cfcmd/route.py",
             "scripts/report/make_handoff.py", "scripts/cfcmd/report.py")
    FORBIDDEN = ("subprocess", "urllib", "socket", "http.client", "requests",
                 "os.system", "os.popen")

    def test_no_module_in_this_package_can_reach_the_network(self):
        for rel in self.FILES:
            text = (SKILL / rel).read_text(encoding="utf-8")
            code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
            for name in self.FORBIDDEN:
                with self.subTest(file=rel, name=name):
                    self.assertNotIn(name, code)

    def test_the_gh_command_is_shell_quoted(self):
        command = route_mod.gh_create_command(
            "/tmp/a b.md", "Exercise 1.2.3: it's wrong", ["faithfulness"], repo="o/n")
        self.assertIn("'/tmp/a b.md'", command)
        self.assertNotIn("it's wrong", command.replace("'\\''", "\x00"))


# ---------------------------------------------------------------------------
# critic#1, second half -- the collision hand-off wave 1 left open
# ---------------------------------------------------------------------------

class TestACollidingFindingIdIsHeldBack(Fixture):
    """``finding_id`` does not hash the claim, so two defects can share one."""

    def setUp(self):
        super().setUp()
        # Same run, source, item, kind and declarations; different concept. The
        # id is sha256(run, source, item, kind, sorted decls), so these collide.
        for name, concept in (("collide-a", "the first claim"), ("collide-b", "the second claim")):
            compat.write_json(
                os.path.join(self.root, "corpus", "hunt", "%s.json" % name),
                finding("Exercise 2.1.1", decls=["%s:Book.outerMeasure_mono" % COMPANION],
                        concept=concept))

    def colliding_id(self) -> str:
        rows = findings_mod.load_findings([os.path.join(self.root, "corpus", "hunt")])
        collisions = findings_mod.id_collisions(rows)
        self.assertEqual(len(collisions), 1, "the fixture must collide exactly once")
        return collisions[0]["finding_id"]

    def adjudicate_everything(self) -> str:
        """A ledger row for EVERY finding, including one for the colliding id.

        Every other row therefore has a verdict, so the only thing that can keep
        this file UNCHALLENGED is the collision itself -- which is what makes the
        export test a guard rather than an accident of the fixture.
        """
        rows = findings_mod.load_findings([os.path.join(self.root, "corpus", "hunt")])
        write_ledger(self.root, [
            ledger_row(fid, item, verdict="CONFIRMED_CONTENT")
            for fid, item in sorted({(r["finding_id"], r["item"]) for r in rows})
        ])
        return self.colliding_id()

    def test_both_rows_are_held_back_rather_than_both_challenged(self):
        fid = self.colliding_id()
        write_ledger(self.root, [ledger_row(fid, "Exercise 2.1.1",
                                            verdict="CONFIRMED_CONTENT")])
        routed = self.route()["routed"]
        rows = [r for r in routed["rows"] if r["finding_id"] == fid]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row["id_collision"])
            self.assertFalse(row["challenged"], "one ledger row cannot adjudicate two claims")
            self.assertNotIn("ledger_verdict", row)
            self.assertIn("2 findings share this finding_id", row["ledger_held_back"])
            self.assertIn("cf harvest", row["ledger_held_back"])
        self.assertEqual(routed["meta"]["counts"]["id_collision_rows"], 2)
        self.assertEqual(routed["meta"]["counts"]["ledger_held_back"], 2)
        self.assertTrue(routed["meta"]["id_collisions"])

    def test_the_file_stays_unchallenged_so_nothing_is_exported(self):
        fid = self.adjudicate_everything()
        routed = self.route()["routed"]
        challenged = {r["finding_id"] for r in routed["rows"] if r.get("challenged")}
        self.assertNotIn(fid, challenged)
        self.assertEqual(routed["meta"]["counts"]["unchallenged"], 2,
                         "the two colliding rows, and only those")
        self.assertEqual(routed["meta"]["status"], route_mod.STATUS_UNCHALLENGED)
        out = os.path.join(self.root, "out")
        with self.assertRaises(route_mod.UpstreamRefused):
            route_mod.emit_upstream(routed, out, companion=COMPANION, workspace=self.root)
        self.assertFalse(os.path.exists(out), "nothing may be written on a refusal")

    def test_route_and_ledger_coverage_agree_about_the_colliding_id(self):
        fid = self.colliding_id()
        rows = [ledger_row(fid, "Exercise 2.1.1", verdict="CONFIRMED_CONTENT")]
        write_ledger(self.root, rows)
        mine = findings_mod.rows_naming(
            findings_mod.load_findings([os.path.join(self.root, "corpus", "hunt")]), COMPANION)
        coverage = vl.ledger_coverage(rows, mine)
        routed = self.route()["routed"]
        challenged = {r["finding_id"] for r in routed["rows"] if r.get("challenged")}
        self.assertNotIn(fid, challenged)
        self.assertEqual({c["finding_id"] for c in coverage["collisions"]}, {fid})
        self.assertIn(fid, set(coverage["missing_ids"]))

    def test_a_withdrawn_collision_is_counted_the_way_the_other_figure_is(self):
        """`ledger_held_back` and `id_collision_rows` count ONE population.

        A withdrawn row is held back from nobody, so counting it in the first
        figure and not the second made two numbers printed side by side disagree.
        """
        for name in ("anchor-a", "anchor-b"):
            compat.write_json(
                os.path.join(self.root, "corpus", "hunt", "%s.json" % name),
                finding("Lemma 1.4.1", kind="PAGE_ANCHOR", severity="low",
                        decls=["%s:Book.outerMeasure_mono" % COMPANION],
                        concept="the page differs (%s)" % name))
        rows = findings_mod.load_findings([os.path.join(self.root, "corpus", "hunt")])
        anchors = [r for r in rows if r["kind"] == "PAGE_ANCHOR"]
        self.assertEqual(len({r["finding_id"] for r in anchors}), 1, "they must collide")
        # A ledger row for BOTH colliding ids: the open CONTENT pair from setUp
        # and this withdrawn PAGE_ANCHOR pair.
        content = next(r for r in rows if r["item"] == "Exercise 2.1.1")
        write_ledger(self.root, [
            ledger_row(content["finding_id"], "Exercise 2.1.1", verdict="CONFIRMED_CONTENT"),
            ledger_row(anchors[0]["finding_id"], "Lemma 1.4.1",
                       verdict="CONFIRMED_CONTENT", kind="PAGE_ANCHOR"),
        ])
        routed = self.route()["routed"]
        counts = routed["meta"]["counts"]
        withdrawn = [r for r in routed["rows"]
                     if r["finding_id"] == anchors[0]["finding_id"]]
        self.assertEqual(len(withdrawn), 2)
        self.assertTrue(all(r.get("withdrawn") for r in withdrawn),
                        "an LLM-emitted PAGE_ANCHOR row with no anchor check is withdrawn")
        self.assertEqual(counts["id_collision_rows"], 2, "the open pair, not the withdrawn one")
        self.assertEqual(counts["ledger_held_back"], counts["id_collision_rows"],
                         "the two withdrawn rows are held back from nobody")

    def test_the_owner_message_says_how_many_rows_are_held_back(self):
        fid = self.colliding_id()
        write_ledger(self.root, [ledger_row(fid, "Exercise 2.1.1",
                                            verdict="CONFIRMED_CONTENT")])
        self.route()
        with open(os.path.join(self.root, "ledger", "routed",
                               "message.%s.md" % COMPANION), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("2 row(s) are held back", text)
        self.assertIn("share one `finding_id`", text)


# ---------------------------------------------------------------------------
# first-run#6 -- one spelling, with the workspace resolved
# ---------------------------------------------------------------------------

class TestThePrintedInvocationIsCopyPasteable(Fixture):
    def test_the_challenge_command_names_cf_and_a_resolved_workspace(self):
        meta = self.route()["routed"]["meta"]
        command = meta["challenge_command"]
        self.assertTrue(command.startswith("cf adjudicate --workspace %s " % self.root), command)
        self.assertNotIn("<workspace>", command)
        self.assertNotIn("scripts/cf.py", command)

    def test_no_placeholder_reaches_the_persisted_artifacts(self):
        """The verifier's extra fact: the broken string was written, not only printed."""
        res = cf("route", cwd=self.root)          # no --workspace, cwd IS the workspace
        self.assertEqual(res.returncode, 0, res.stderr)
        for rel in ("ledger/routed/findings.%s.jsonl" % COMPANION,
                    "ledger/routed/message.%s.md" % COMPANION):
            with self.subTest(file=rel):
                text = pathlib.Path(self.root, rel).read_text(encoding="utf-8")
                self.assertNotIn("<workspace>", text)
                self.assertNotIn("python3 scripts/cf.py", text)
                self.assertIn("cf adjudicate --workspace %s" % self.root, text)

    def test_the_command_is_resolved_when_route_runs_from_a_subdirectory(self):
        """`cf route` from a subdirectory still finds the workspace AND resolves it."""
        deep = os.path.join(self.root, "reports")
        os.makedirs(deep, exist_ok=True)
        res = cf("route", cwd=deep)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("cf adjudicate --workspace %s " % self.root, res.stdout)

    def test_the_withdrawal_reason_names_a_command_that_resolves(self):
        reason = route_mod._withdrawal(
            {"kind": "PAGE_ANCHOR", "item": "Exercise 1.2.3"}, {}, set(), SOURCE_ID)
        self.assertIn("cf pair", reason)
        self.assertNotIn("scripts/cf.py", reason)

    def test_every_cf_command_this_package_prints_really_exists(self):
        res = cf("route", cwd=self.root)
        text = res.stdout + res.stderr
        text += pathlib.Path(self.root, "ledger", "routed",
                             "message.%s.md" % COMPANION).read_text(encoding="utf-8")
        report = cf("report", cwd=self.root)
        self.assertEqual(report.returncode, 0, report.stderr)
        text += report.stdout + report.stderr
        for path in sorted(pathlib.Path(self.root, "reports").glob("handoff.*.md")):
            text += path.read_text(encoding="utf-8")
        printed = sorted(set(re.findall(r"\bcf ([a-z][a-z-]+)", text)))
        self.assertTrue(printed, "the package prints no command at all?")
        for name in printed:
            with self.subTest(command=name):
                helped = cf(name, "--help")
                self.assertEqual(helped.returncode, 0,
                                 "`cf %s` is printed but does not exist" % name)


# ---------------------------------------------------------------------------
# critic#5 -- the handoff's denominator
# ---------------------------------------------------------------------------

class CoverageFixture(Fixture):
    CHAPTERS = "    chapters: [1]\n"
    #: {item: the companions that formalise it}; only the first item is a PAIR
    PAIR_INDEX = {"Exercise 1.2.3": ["ours", "adam"], "Lemma 1.4.1": ["ours"]}

    def setUp(self):
        super().setUp()
        write_reconciliation(
            self.root, SOURCE_ID,
            chapters=[1], items_in_book=3, items_in_scope=2,
            items_in_scope_and_any_repo=2, task_universe=2,
            task_universe_items=["Exercise 1.2.3", "Lemma 1.4.1"],
            pairs=1, items_in_scope_uncovered=[])
        if self.PAIR_INDEX is not None:
            write_pair_index(self.root, SOURCE_ID, self.PAIR_INDEX)
        self.route()

    def handoff(self, **kwargs) -> dict:
        return handoff_mod.make_handoff(self.root, date="2026-01-01", **kwargs)


class TestTheHandoffHasADenominator(CoverageFixture):
    def test_the_sources_table_carries_the_scope(self):
        text = self.handoff()["text"]
        self.assertIn("| source | sha256 | edition | offset | chapters audited | items in scope |",
                      text)
        self.assertIn("| 1 / 2 | 2 / 3 |", text)

    def test_the_coverage_section_counts_per_source_and_chapter(self):
        text = self.handoff()["text"]
        self.assertIn("## 0. How much of each source was audited", text)
        self.assertIn("| source | chapter | items in source | in scope | in task universe | "
                      "paired | examined | gated | with findings | never examined |", text)
        # chapter 1: 2 in scope, 2 in the task universe, 1 of them PAIRED,
        # 2 examined, 2 gated, 2 with findings, 0 missed
        self.assertIn("| `%s` | 1 | — | 2 | 2 | 1 | 2 | 2 | 2 | 0 |" % SOURCE_ID, text)

    def test_the_task_universe_is_not_called_paired(self):
        """225 paired and 383 in the task universe are different figures.

        The pinned baseline describes its 383 as "225 paired + 158
        single-source", so a column headed "paired" showing the task universe
        claims a cross-check 158 items never had.
        """
        block = self.handoff()["context"]["_coverage"]["sources"][0]
        self.assertEqual(block["items_in_task_universe"], 2)
        self.assertEqual(block["items_paired"], 1, "only one item has two companions")
        self.assertNotEqual(block["items_paired"], block["items_in_task_universe"],
                            "the fixture must distinguish the two figures")
        text = self.handoff()["text"]
        self.assertIn("2 in the differential's task universe, 1 paired by two or more "
                      "companions", text)

    def test_the_paired_figure_is_recomputed_from_the_pairing_index(self):
        by_chapter = self.handoff()["context"]["_coverage"]["sources"][0]["by_chapter"]
        self.assertEqual(by_chapter["1"]["in_task_universe"], 2)
        self.assertEqual(by_chapter["1"]["paired"], 1)

    def test_chapter_two_is_out_of_scope_and_is_not_counted(self):
        coverage = self.handoff()["context"]["_coverage"]
        block = coverage["sources"][0]
        self.assertEqual(block["items_in_source"], 3)
        self.assertEqual(block["items_in_scope"], 2)
        self.assertEqual(block["chapters_audited"], [1])
        self.assertNotIn("2", block["by_chapter"])

    def test_the_tldr_carries_the_denominator_in_the_same_sentence(self):
        text = self.handoff()["text"]
        self.assertIn("Coverage: `%s`: 2 of 2 item(s) in scope examined (3 in the source)"
                      % SOURCE_ID, text)

    def test_coverage_json_is_machine_readable(self):
        self.handoff()
        with open(os.path.join(self.root, "reports", "coverage.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        block = doc["sources"][0]
        self.assertEqual(block["source_id"], SOURCE_ID)
        self.assertEqual(block["items_examined"], 2)
        self.assertEqual(block["items_with_findings"], 2)
        self.assertEqual(block["items_never_examined"], 0)

    def test_the_second_source_is_reported_as_unaudited_not_omitted(self):
        doc = self.handoff()["context"]["_coverage"]
        ids = [s["source_id"] for s in doc["sources"]]
        self.assertEqual(ids, [SOURCE_ID, OTHER_SOURCE_ID])
        other = doc["sources"][1]
        self.assertEqual(other["items_examined"], 0)
        self.assertFalse(other["under_audit"], "no companion is bound to it")
        self.assertEqual(other["role"], "reference")

    def test_a_reference_edition_does_not_make_the_report_partial(self):
        """Both real workspaces carry an earlier draft nobody is bound to.

        Counting it as scope missing made the first paragraph's warning fire on
        every report ever written, which is how a warning stops meaning anything.
        """
        first = self.handoff()["text"].split("## 0.")[0]
        self.assertNotIn("`%s` (no denominator computed)" % OTHER_SOURCE_ID, first)
        self.assertIn("Not under audit (no companion is bound to them): `%s`." % OTHER_SOURCE_ID,
                      first)
        self.assertIn("not under audit — no companion is bound to it (`role: reference`)",
                      self.handoff()["text"])

    def test_the_chapter_fraction_names_what_the_denominator_is(self):
        """"chapters audited 1 of 1, 2" read as one figure over another."""
        text = self.handoff()["text"]
        self.assertIn("chapters audited 1 of chapters 1, 2;", text)
        self.assertNotIn("chapters audited 1 of 1, 2", text)

    def test_the_command_names_the_files_it_wrote(self):
        """`cf report` wrote two more artifacts and named neither (RULES: --help)."""
        res = cf("report", cwd=self.root)
        self.assertEqual(res.returncode, 0, res.stderr)
        for rel in ("reports/coverage.json", "reports/unexamined.%s.json" % SOURCE_ID):
            with self.subTest(file=rel):
                path = os.path.join(self.root, rel)
                self.assertTrue(os.path.exists(path), rel)
                self.assertIn("wrote %s" % path, res.stdout)
        helped = cf("report", "--help")
        self.assertIn("reports/coverage.json", helped.stdout)
        self.assertIn("reports/unexamined.<source_id>.json", helped.stdout)
        self.assertNotIn("Two outputs", helped.stdout)


class TestAMissingPairingIndexIsNotZeroPairs(CoverageFixture):
    PAIR_INDEX = None       # `cf pair` never ran, so nothing knows what paired

    def test_the_paired_figure_is_uncomputed_rather_than_zero(self):
        block = self.handoff()["context"]["_coverage"]["sources"][0]
        self.assertIsNone(block["items_paired"])
        text = self.handoff()["text"]
        self.assertIn("TODO(paired)", text)
        self.assertIn("no `pairs/%s/index.json`" % SOURCE_ID, text)


class TestUnexaminedItemsAreNamed(CoverageFixture):
    CHAPTERS = ""            # the whole book is in scope, so a missed item shows

    def setUp(self):
        super().setUp()
        # widen the scope to both chapters, so one item in scope was never examined
        write_reconciliation(
            self.root, SOURCE_ID, chapters=None, items_in_book=3, items_in_scope=3,
            items_in_scope_and_any_repo=2, task_universe=2,
            task_universe_items=["Exercise 1.2.3", "Lemma 1.4.1"])

    def test_the_names_are_written_to_reports(self):
        self.handoff()
        path = os.path.join(self.root, "reports", "unexamined.%s.json" % SOURCE_ID)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["items"], ["Exercise 2.1.1"])
        self.assertEqual(doc["count"], 1)
        self.assertEqual(doc["by_chapter"]["2"], ["Exercise 2.1.1"])

    def test_the_report_says_so_in_its_first_paragraph(self):
        text = self.handoff()["text"]
        first = text.split("## 0.")[0]
        self.assertIn("covers PART of its sources", first)
        self.assertIn("1 never examined", first)

    def test_the_report_points_at_the_file_holding_the_names(self):
        self.assertIn("reports/unexamined.<source_id>.json", self.handoff()["text"])


class TestAnUngatedRunIsNamed(CoverageFixture):
    GATE_OK = None          # a RECEIPT with no gate block at all

    def test_its_items_count_as_examined_but_not_as_gated(self):
        block = self.handoff()["context"]["_coverage"]["sources"][0]
        self.assertEqual(block["items_examined"], 2)
        self.assertEqual(block["items_gated"], 0)
        self.assertEqual(block["ungated_runs"], ["hunt"])

    def test_the_prose_names_the_run_and_the_command(self):
        text = self.handoff()["text"]
        self.assertIn("ungated run(s) `hunt`", text)
        self.assertIn("cf harvest <run-id>", text)


class TestAStaleReconciliationIsNamed(CoverageFixture):
    def setUp(self):
        super().setUp()
        write_reconciliation(self.root, SOURCE_ID, chapters=[1], items_in_book=3,
                             items_in_scope=99, task_universe=2,
                             task_universe_items=["Exercise 1.2.3"])

    def test_the_two_artifacts_disagreeing_is_said_out_loud(self):
        text = self.handoff()["text"]
        self.assertIn("one of them is stale", text)
        self.assertIn("cf pair", text)


class TestAWorkspaceWithNoItemsJsonSaysItHasNoDenominator(Fixture):
    def test_the_scope_note_refuses_to_imply_coverage(self):
        os.remove(os.path.join(self.root, "sources", SOURCE_ID, "items.json"))
        self.route()
        text = handoff_mod.make_handoff(self.root, date="2026-01-01")["text"]
        self.assertIn("no denominator computed", text)
        self.assertIn("no `items.json` yet", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
