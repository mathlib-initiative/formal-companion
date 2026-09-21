"""The final review's privacy findings: what leaves the workspace, and whose git runs.

Ten findings, every one reproduced against the code before it was changed:

* **FR#3** -- `cf share export` scrubbed a list of seven prose keys at depth one.
  The v2 ledger writer (`ledger/transcribe.py`) puts the same sentence in
  `summary`, `refutation_text`, `lens_disagreement`, `recovery_argument` and
  `dissent[].argument`, and a routed row's `witness.why` is two levels down, so
  the sentence redacted out of `reason` travelled five more times in the same
  bundle while the manifest said `quotes: redacted`.  `share.redact.redact_prose`
  is now a walk of every string of a row whose DEFAULT is to redact.
* **FR#4 / FR#6 / FR#7** -- `cf route --github` addresses `public`, and the
  excerpt policy governed `book_quote` alone: the body carried the book's own
  sentence in `concept`, `reasoning`, `suggested_fix` and `witness`, published a
  quote of twelve words or fewer WHOLE, and passed a row's absolute checkout path
  through while `_issue_body` promised it carried none.  One redactor now serves
  both emitters, and a checked policy with no pinned text to check against is
  refused rather than drafted.
* **FR#5 / FR#9 / FR#18** -- `cf quotes hydrate --out` asked an arbitrary repo
  whether it ignored the operator's path, with plain `git check-ignore`, which
  refreshes the index, which runs that repository's own `core.fsmonitor`.  The
  safety check was the execution vector.  Both calls go through `cflib.gitsafe`
  now, and a `--out` inside a companion repo is refused before any git runs.
* **FR#28** -- the export printed `next: cf share import <bundle>` for a bundle
  the import refuses with rc=2.
* **FR#32** -- a "redacted" record handed over 12 of a 14-word quote.
* **FR#36** -- the 40% first-pass figure reached a drafted issue body with no
  denominator beside it.

Everything here is synthetic: the book sentences are invented and no companion's
Lean and no real campaign is read (CLAUDE.md S1).  The two baseline-gated tests
skip cleanly without `CF_BASELINE_CORPUS`.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
# See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_jsonl, write_json, write_jsonl  # noqa: E402
from cflib.workspace import Workspace  # noqa: E402
from cfcmd import init as init_mod  # noqa: E402
from cfcmd import quotes as quotes_mod  # noqa: E402
from ledger import transcribe  # noqa: E402
from route import route as route_mod  # noqa: E402
from share import bundle as bundle_mod  # noqa: E402
from share import hydrate as hydrate_mod  # noqa: E402
from share import merge as merge_mod  # noqa: E402
from share import redact as redact_mod  # noqa: E402

SOURCE_ID = "a-book-1st-edition"
SOURCE_SHA = "a" * 64
COMPANION = "adam"

#: the sentence a finding quotes; invented, not a line of any real book
SENTENCE = ("Let E be an elementary set. Then the outer measure of every subset of E "
            "is at most the outer measure of E itself.")
#: ten words, which the excerpt policy used to publish whole
SHORT = "Every compact set is Lebesgue measurable and has finite measure"
#: thirteen, where six words from each end is twelve of the thirteen
THIRTEEN = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu"
#: a worker citing the file it read, which is where the baseline's one absolute
#: path lives (`share/redact.py`'s own measurement)
PATHY = ("the statement at /Users/alice/a-checkout/Measure/Unsigned/"
         "UnsignedMeasurability.lean:324 quantifies the wrong way")

PAGE_ONE = "Chapter 1. Elementary measure\n\nWe begin with the elementary theory.\n"
PAGE_TWO = f"Exercise 1.2.3. {SENTENCE}\n"
PAGE_THREE = f"Lemma 1.4.1. {SHORT}. {THIRTEEN}.\n"
SOURCE_TEXT = "\f".join([PAGE_ONE, PAGE_TWO, PAGE_THREE])


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path.resolve()   # /var -> /private/var on macOS: cflib resolves its paths


def lines_saying(text: str, phrase: str) -> list[str]:
    """``"<line no>: <line>"`` for each line of *text* carrying *phrase*.

    A negative assertion about a whole document prints the whole document when it
    fails, which buries the one line the reader needs.
    """
    return [f"{n}: {line.strip()}" for n, line in enumerate(text.splitlines(), 1)
            if phrase in line]


def walk_strings(value, path: str = ""):
    """``(json path, string)`` for every string in *value*, at any depth."""
    if isinstance(value, str):
        if value.strip():
            yield path, value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from walk_strings(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for n, inner in enumerate(value):
            yield from walk_strings(inner, f"{path}[{n}]")


def make_workspace(case: unittest.TestCase, *, text: str | None = SOURCE_TEXT,
                   companion_repo: Path | None = None) -> Workspace:
    """A workspace with one companion, one source and (usually) its pinned text."""
    root = scratch(case) / "ws"
    repo = companion_repo if companion_repo is not None else root.parent / "companion-repo"
    repo.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True)
    (root / "workspace.yaml").write_text(
        "version: 2\n"
        "workspace:\n  name: rf\n"
        "companions:\n"
        f"  {COMPANION}:\n"
        f"    repo: {repo}\n"
        "    adapter: docstring_tags\n"
        f"    sources: [{SOURCE_ID}]\n"
        f"    anchor_source: {SOURCE_ID}\n"
        "sources:\n"
        f"  {SOURCE_ID}:\n"
        f'    sha256: "{SOURCE_SHA}"\n'
        "    title: A Book\n"
        '    edition: "first edition"\n'
        "    offset: 16\n"
        "    role: audited\n"
        "hosts:\n  allowlist: []\n", encoding="utf-8")
    workspace = Workspace.load(root)
    workspace.paths.ensure_skeleton()
    workspace.paths.companion_dir(COMPANION).mkdir(parents=True, exist_ok=True)
    if text is not None:
        workspace.paths.source_dir(SOURCE_ID).mkdir(parents=True, exist_ok=True)
        workspace.paths.source_text(SOURCE_ID).write_text(text, encoding="utf-8")
    write_json(workspace.paths.tally_json("run-1"), {"rows": 1, "items": 1})
    return workspace


def routed_row(**over) -> dict:
    row = {
        "finding_id": "c3" * 32,
        "run_id": "run-1",
        "source_id": SOURCE_ID,
        "source_sha": SOURCE_SHA,
        "item": "Exercise 1.2.3",
        "kind": "CONTENT",
        "severity": "high",
        "repo": COMPANION,
        "unfaithful_decls": [f"{COMPANION}:Book.outerMeasure_mono"],
        "other_decls": [],
        "faithful": [],
        "page": 24,
        "concept": "monotonicity of outer measure",
        "book_quote": SENTENCE,
        "reasoning": "the Lean statement drops the measurability hypothesis",
        "suggested_fix": "add the hypothesis",
        "provenance": "worker",
        "source_file": "corpus/run-1/exercise-1-2-3.json",
        "challenged": True,
        "ledger_verdict": "CONFIRMED_CONTENT",
        "ledger_method": "workflow:adjudicate",
    }
    row.update(over)
    return row


def routed(rows) -> dict:
    """A CHALLENGED routed payload, the shape both emitters take."""
    return {"rows": list(rows),
            "meta": {"repo": COMPANION, "status": route_mod.STATUS_CHALLENGED,
                     "schema": "routed-v2", "rule": route_mod.ROUTING_RULE,
                     "sources": {SOURCE_ID: SOURCE_SHA},
                     "counts": {"rows": len(list(rows)), "items": 1, "challenged": 1,
                                "unchallenged": 0}}}


# ==========================================================================
# FR#3 -- the scrubber scanned a list of key names, at depth one
# ==========================================================================
class EveryProseFieldIsScrubbedTest(unittest.TestCase):
    """The rows the repo's OWN v2 writer produces, exported with the defaults.

    `ledger.transcribe.rows_from_adjudication` is what `cf adjudicate` calls, and
    of the 24 keys it writes exactly one (`reason`) was in `PROSE_FIELDS`.  A
    fixture that writes the v1 shape by hand cannot see that, which is why the
    first version of this check passed while five copies of the sentence left.
    """

    def setUp(self):
        self.workspace = make_workspace(self)
        self.index = redact_mod.SourceIndex([SOURCE_TEXT])
        rows = transcribe.rows_from_adjudication(
            {"findings": [{
                "finding_id": "c3" * 32,
                "item": "Exercise 1.2.3",
                "concept": SENTENCE,
                "reconciled": {"verdict": "CONVENTION", "severity": "low",
                               "rationale": SENTENCE, "refutation_text": SENTENCE,
                               "lens_disagreement": SENTENCE, "recovery_argument": SENTENCE},
                "skeptics": [{"role": "convention-skeptic", "overturned": True,
                              "confidence": 0.9, "proposed_verdict": "CONVENTION",
                              "argument": SENTENCE}],
            }]},
            companion=COMPANION, source_id=SOURCE_ID, source_sha=SOURCE_SHA)
        self.row = rows[0]
        write_jsonl(self.workspace.paths.accepted_jsonl(COMPANION), rows)
        write_jsonl(self.workspace.paths.routed_jsonl(COMPANION),
                    [routed_row(witness={"object": "E", "why": SENTENCE},
                                evidence=["nothing here", SENTENCE])],
                    meta={"repo": COMPANION, "status": "CHALLENGED", "schema": "routed-v2"})
        self.out = scratch(self) / "bundle"
        self.manifest = bundle_mod.export_bundle(self.workspace, self.out)

    def exported(self):
        for path in sorted(self.out.rglob("*.jsonl")):
            for row in read_jsonl(path):
                if "_meta" not in row:
                    yield path.name, row

    def test_the_writer_really_does_put_the_sentence_in_five_more_keys(self):
        """The fixture is worth nothing unless the unredacted row fails this."""
        carrying = sorted(
            path for path, value in walk_strings(self.row)
            if self.index.longest_span(redact_mod.normalise_quote(value)))
        self.assertEqual(carrying, ["dissent[0].argument", "lens_disagreement", "reason",
                                    "recovery_argument", "refutation_text", "summary"])

    def test_no_string_of_an_exported_row_carries_a_run_of_the_book(self):
        for name, row in self.exported():
            for path, value in walk_strings(row):
                if path.startswith("book_quote") or path.startswith(redact_mod.SPANS_KEY):
                    continue
                self.assertIsNone(self.index.longest_span(redact_mod.normalise_quote(value)),
                                  f"{name} {path}")

    def test_the_manifest_counts_every_span_that_was_taken_out(self):
        """It reported 1 while 5 more copies of that sentence left in the same file."""
        ledger = [row for name, row in self.exported() if name.startswith("accepted")]
        self.assertEqual(len(redact_mod.prose_spans(ledger[0])), 6)
        # 6 from the ledger row + reasoning/witness/evidence from the routed row
        self.assertEqual(self.manifest["counts"]["prose_spans"], 8)

    def test_a_span_record_names_the_json_path_it_came_from(self):
        ledger = [row for name, row in self.exported() if name.startswith("accepted")]
        fields = sorted(span["field"] for span in redact_mod.prose_spans(ledger[0]))
        self.assertIn("dissent[0].argument", fields)
        self.assertIn("summary", fields)
        routed_rows = [row for name, row in self.exported() if name.startswith("findings")]
        self.assertIn("witness.why",
                      [span["field"] for span in redact_mod.prose_spans(routed_rows[0])])

    def test_a_prose_field_a_writer_made_a_list_is_scanned_too(self):
        """`evidence` IS in PROSE_FIELDS, and was skipped the moment it was a list."""
        routed_rows = [row for name, row in self.exported() if name.startswith("findings")]
        self.assertIn("evidence[1]",
                      [span["field"] for span in redact_mod.prose_spans(routed_rows[0])])
        self.assertEqual(routed_rows[0]["evidence"][0], "nothing here")

    def test_the_redaction_record_is_not_perforated_by_the_walk(self):
        """`book_quote` and `redacted_spans` are records this module wrote.

        Their edge words are disclosed by design, and they are above the prose
        floor, so a walk that treated them as prose would nest a record inside a
        record and cost the hydration its anchor.
        """
        routed_rows = [row for name, row in self.exported() if name.startswith("findings")]
        record = routed_rows[0]["book_quote"]
        self.assertEqual(record["sha256"], redact_mod.quote_digest(SENTENCE))
        self.assertNotIn("redacted", str(record.get("first_words", "")))
        for span in redact_mod.prose_spans(routed_rows[0]):
            self.assertTrue(redact_mod.is_redacted(span["quote"]))

    def test_every_redacted_span_still_hydrates_out_of_the_holders_own_book(self):
        rows = [row for _, row in self.exported()]
        reports, counts = hydrate_mod.hydrate_rows(hydrate_mod.redacted_rows(rows),
                                                   {SOURCE_ID: SOURCE_TEXT})
        self.assertEqual(counts[hydrate_mod.HYDRATED], counts["redacted"])
        self.assertEqual([r for r in reports if r["status"] != hydrate_mod.HYDRATED], [])


class TheWalkKeepsItsSkipListHonestTest(unittest.TestCase):
    def test_a_key_nobody_thought_of_is_redacted_rather_than_carried(self):
        index = redact_mod.SourceIndex([SOURCE_TEXT])
        row = {"item": "Exercise 1.2.3", "a_key_added_next_year": SENTENCE}
        out = redact_mod.redact_prose(row, index)
        self.assertNotIn(SENTENCE, out["a_key_added_next_year"])
        self.assertEqual([s["field"] for s in redact_mod.prose_spans(out)],
                         ["a_key_added_next_year"])

    def test_an_id_or_an_enum_is_left_exactly_as_written(self):
        index = redact_mod.SourceIndex([SOURCE_TEXT])
        row = routed_row()
        out = redact_mod.redact_prose(row, index)
        for key in ("finding_id", "item", "kind", "severity", "source_sha", "repo"):
            self.assertEqual(out[key], row[key], key)
        self.assertNotIn(redact_mod.SPANS_KEY, out, "nothing was redacted out of this row")

    def test_a_page_dump_names_the_path_it_was_found_at(self):
        index = redact_mod.SourceIndex([SOURCE_TEXT])
        dump = " and ".join([SENTENCE] * (redact_mod.MAX_SPANS_PER_FIELD + 2))
        with self.assertRaises(ValueError) as ctx:
            redact_mod.redact_prose({"witness": {"why": dump}}, index)
        self.assertIn("witness.why", str(ctx.exception))


# ==========================================================================
# FR#32 -- the edges of a "redacted" quote were most of the quote
# ==========================================================================
class TheEdgeWordsAreProportionateTest(unittest.TestCase):
    """A single-fixture assertion cannot catch a boundary rule; this is a sweep."""

    def test_no_record_discloses_half_its_words_or_a_run_of_the_source(self):
        for count in range(1, 41):
            words = ["w%02d" % n for n in range(count)]
            record = redact_mod.redact_quote(" ".join(words))
            with self.subTest(words=count):
                if "first_words" not in record:
                    self.assertIn("edges_withheld", record)
                    continue
                disclosed = (len(record["first_words"].split())
                             + len(record["last_words"].split()))
                self.assertLessEqual(disclosed, count // 2)
                self.assertLess(max(len(record["first_words"]), len(record["last_words"])),
                                redact_mod.PROSE_SPAN_MIN)

    def test_the_boundary_case_the_flat_six_words_handed_over(self):
        """Fourteen words: the old rule emitted twelve of them."""
        words = redact_mod.normalise_quote(
            "let e be an elementary set and let f be a second elementary set").split()
        self.assertEqual(len(words), 14)
        record = redact_mod.redact_quote(" ".join(words))
        self.assertEqual(len(record["first_words"].split()), 3)
        self.assertEqual(len(record["last_words"].split()), 3)

    def test_a_short_quote_keeps_its_edges_to_itself(self):
        record = redact_mod.redact_quote(SHORT)
        self.assertNotIn("first_words", record)
        self.assertIn("edges_withheld", record)

    def test_an_edge_that_would_be_a_run_of_the_source_is_shortened(self):
        """One 45-character word at each end: the word count allows an edge, the
        character floor does not."""
        long_word = "supercalifragilistic" * 3
        words = [long_word] + ["tiny"] * 18 + [long_word]
        record = redact_mod.redact_quote(" ".join(words))
        self.assertLess(max(len(record.get("first_words", "")),
                            len(record.get("last_words", ""))),
                        redact_mod.PROSE_SPAN_MIN)

    def test_one_budget_serves_both_emitters(self):
        """`cf route`'s public excerpt and the bundle's record must agree.

        They did not: a 10-word sentence was published WHOLE to a tracker while
        the bundle's own record of it withheld its edges.
        """
        # a value comparison would prove nothing here: both are small ints, which
        # CPython interns, so a literal `EXCERPT_WORDS = 6` would pass it.
        source = (SCRIPTS / "route" / "route.py").read_text(encoding="utf-8")
        self.assertTrue(lines_saying(source, "EXCERPT_WORDS = redact_mod.EDGE_WORDS"),
                        "route's ceiling must BE the redactor's, not a copy of its value")
        words = SENTENCE.split(" ")
        keep = redact_mod.edge_words(words)
        rendered = route_mod.render_quote(SENTENCE, route_mod.QUOTE_EXCERPT, page=24)
        self.assertIn(" ".join(words[:keep]), rendered)
        self.assertNotIn(" ".join(words[:keep + 1]), rendered)


BASELINE_CORPUS = os.environ.get("CF_BASELINE_CORPUS", "")


@unittest.skipUnless(BASELINE_CORPUS, "needs CF_BASELINE_CORPUS")
class RecordedEdgeFiguresTest(unittest.TestCase):
    """The numbers `share.redact.edge_words` records, re-derived from the corpus.

    A figure nobody re-derives goes stale, and these are the figures the budget
    was chosen from: the flat six words handed over 86% of a 14-word quote and
    149 edges at or above the module's own 40-character floor.
    """

    @classmethod
    def setUpClass(cls):
        cls.quotes = []
        for path in sorted(Path(BASELINE_CORPUS).rglob("*.json")):
            if ".git" in path.parts:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (ValueError, OSError):                    # pragma: no cover - stray file
                continue
            stack = [data]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    quote = node.get("book_quote")
                    if isinstance(quote, str) and quote.strip():
                        cls.quotes.append(redact_mod.normalise_quote(quote).split())
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)

    def test_the_corpus_is_the_one_the_comment_was_measured_on(self):
        self.assertEqual(len(self.quotes), 429)
        self.assertEqual(sum(1 for w in self.quotes
                             if len(w) < redact_mod.MIN_WORDS_FOR_EDGES), 39)

    def test_the_flat_six_words_was_the_disclosure_the_comment_records(self):
        long_enough = [w for w in self.quotes if len(w) >= redact_mod.MIN_WORDS_FOR_EDGES]
        share = max(12 / len(w) for w in long_enough)
        longest = max(max(len(" ".join(w[:6])), len(" ".join(w[-6:]))) for w in long_enough)
        over = sum(1 for w in long_enough
                   if max(len(" ".join(w[:6])), len(" ".join(w[-6:])))
                   >= redact_mod.PROSE_SPAN_MIN)
        self.assertEqual(round(100 * share), 86)
        self.assertEqual(longest, 67)
        self.assertEqual(over, 149)

    def test_the_budget_holds_on_every_real_quote(self):
        kept = shortened = 0
        worst = 0.0
        longest = 0
        for words in self.quotes:
            keep = redact_mod.edge_words(words)
            if not keep:
                continue
            kept += 1
            shortened += int(keep < min(redact_mod.EDGE_WORDS, len(words) // 4))
            worst = max(worst, 2 * keep / len(words))
            longest = max(longest, len(" ".join(words[:keep])), len(" ".join(words[-keep:])))
        self.assertEqual(kept, 390)
        self.assertEqual(shortened, 70)
        self.assertEqual(round(100 * worst), 50)
        self.assertEqual(longest, 39)
        self.assertLess(longest, redact_mod.PROSE_SPAN_MIN)


# ==========================================================================
# FR#4 / FR#6 / FR#7 -- the drafts addressed to a tracker anyone can read
# ==========================================================================
class PublicDraftsCarryNoBookProseTest(unittest.TestCase):
    def setUp(self):
        self.workspace = make_workspace(self)
        self.root = str(self.workspace.root)

    def body(self, row, **kwargs) -> str:
        built = route_mod.github_issues(routed([row]), companion=COMPANION,
                                        workspace=self.root, **kwargs)
        return built["issues"][0]["body"]

    def test_the_sentence_is_absent_from_every_prose_line(self):
        body = self.body(routed_row(concept=SENTENCE, reasoning=SENTENCE,
                                    suggested_fix=SENTENCE,
                                    witness={"object": "E", "why": SENTENCE}))
        self.assertNotIn(SENTENCE, body)
        self.assertNotIn(redact_mod.normalise_quote(SENTENCE), body.lower())
        self.assertIn("[redacted:", body, "the marker says the sentence was taken out")

    def test_the_owner_keeps_the_sentence_because_the_owner_owns_the_book(self):
        message = route_mod.owner_message(
            routed([routed_row(reasoning=SENTENCE)]), COMPANION,
            destination="owner", workspace=self.root)
        self.assertIn(SENTENCE, message)

    def test_a_public_owner_message_is_scrubbed_like_an_issue_body(self):
        message = route_mod.owner_message(
            routed([routed_row(reasoning=SENTENCE,
                               witness={"object": "E", "why": SENTENCE})]),
            COMPANION, destination="public", workspace=self.root)
        self.assertNotIn(SENTENCE, message)

    def test_a_workspace_with_no_pinned_text_is_refused_not_drafted(self):
        """And the refusal of a PUBLISHABLE draft offers no way to widen it.

        `--destination owner` settles this refusal by resolving to `full`, which
        on this path is issue bodies carrying the book's own sentence beside the
        `gh issue create --body-file` command `index.json` prints for each --
        i.e. the leak the refusal exists to prevent.
        """
        workspace = make_workspace(self, text=None)
        with self.assertRaises(route_mod.PolicyRefused) as ctx:
            route_mod.github_issues(routed([routed_row()]), companion=COMPANION,
                                    workspace=str(workspace.root))
        message = str(ctx.exception)
        self.assertIn("cf sources refresh", message)
        self.assertNotIn("--destination owner", message)
        self.assertNotIn("--quotes full", message)

    def test_the_owner_draft_may_be_told_where_the_sentence_is_allowed(self):
        """The same refusal on the draft that stays put keeps its escape hatch."""
        workspace = make_workspace(self, text=None)
        with self.assertRaises(route_mod.PolicyRefused) as ctx:
            route_mod.owner_message(routed([routed_row()]), COMPANION,
                                    destination="public", workspace=str(workspace.root))
        self.assertIn("--destination owner", str(ctx.exception))

    def test_a_narrowed_policy_is_told_to_widen_the_policy_not_the_destination(self):
        """`--destination owner --quotes hash`: naming `owner` again changes nothing."""
        workspace = make_workspace(self, text=None)
        with self.assertRaises(route_mod.PolicyRefused) as ctx:
            route_mod.owner_message(routed([routed_row()]), COMPANION,
                                    destination="owner", quotes=route_mod.QUOTE_HASH,
                                    workspace=str(workspace.root))
        message = str(ctx.exception)
        self.assertIn("--quotes full", message)
        self.assertNotIn("--destination owner", message)

    def test_the_prose_scrub_is_keyed_on_the_policy_the_documents_now_name(self):
        """`--destination public --quotes full` scrubs nothing, and says so.

        The switch is the RESOLVED policy, not the destination name. The help and
        POLICY.md promised it destination-first, which was false on this path.
        """
        workspace = make_workspace(self, text=None)
        message = route_mod.owner_message(
            routed([routed_row(reasoning=SENTENCE)]), COMPANION, destination="public",
            quotes=route_mod.QUOTE_FULL, workspace=str(workspace.root))
        self.assertIn(SENTENCE, message)
        help_text = (SCRIPTS / "cfcmd" / "route.py").read_text(encoding="utf-8")
        self.assertTrue(lines_saying(help_text, "Any quote policy but ``full`` also scrubs"),
                        "the help must key the promise on the resolved policy")
        self.assertEqual(lines_saying(help_text, "A destination other than ``owner`` also"), [],
                         "the help keyed the promise on the destination, not the policy")
        policy_md = (REPO / "POLICY.md").read_text(encoding="utf-8")
        self.assertTrue(lines_saying(policy_md, "Unless the destination's resolved policy is"),
                        "POLICY.md S4 must key the promise on the resolved policy")

    def test_the_redaction_record_is_described_the_same_way_everywhere(self):
        """A5 promised "first/last six words", which stopped being true here."""
        traps = (REPO / "references" / "traps.md").read_text(encoding="utf-8")
        self.assertEqual(lines_saying(traps, "first/last six words"), [],
                         "A5 still describes the record the flat six-word rule wrote")
        self.assertTrue(lines_saying(traps, "up to a quarter of the quote's words from each"),
                        "A5 must describe the record `share.redact.edge_words` writes")
        # The copy that outlived the documents was shipped CODE: `cf share export
        # --help` still handed an operator the flat six words a round after every
        # document had been corrected, so the whole of scripts/ answers here too.
        for path in sorted(SCRIPTS.rglob("*.py")):
            said = lines_saying(path.read_text(encoding="utf-8"), "first/last six")
            self.assertEqual(said, [], f"{path.relative_to(REPO)} states the flat six "
                                       f"words the behaviour fix deleted: {said}")

    def test_the_owner_draft_still_works_without_the_pinned_text(self):
        """The refusal is about a policy that must be checked, not about text."""
        workspace = make_workspace(self, text=None)
        message = route_mod.owner_message(routed([routed_row()]), COMPANION,
                                          destination="owner",
                                          workspace=str(workspace.root))
        self.assertIn(SENTENCE, message)

    def test_a_short_sentence_is_withheld_rather_than_published_whole(self):
        for quote in (SHORT, THIRTEEN):
            with self.subTest(quote=quote):
                rendered = route_mod.render_quote(quote, route_mod.QUOTE_EXCERPT, page=24)
                self.assertNotIn(quote, rendered)
                self.assertIn("withheld", rendered)
                words = quote.split(" ")
                for start in range(len(words) - route_mod.EXCERPT_WORDS):
                    run = " ".join(words[start:start + route_mod.EXCERPT_WORDS + 1])
                    self.assertNotIn(run, rendered)

    def test_a_short_quote_does_not_reach_an_issue_body(self):
        body = self.body(routed_row(book_quote=SHORT))
        self.assertNotIn(SHORT, body)
        self.assertIn("quote withheld for this destination", body)

    def test_an_absolute_path_in_a_rows_prose_keeps_its_file_and_line_only(self):
        body = self.body(routed_row(reasoning=PATHY))
        self.assertIn(".../UnsignedMeasurability.lean:324", body)
        self.assertNotIn("/Users/alice", body)
        self.assertNotRegex(body, r"(?<![\w<])/(Users|home)/")

    def test_the_owner_draft_carries_no_absolute_path_either(self):
        """`finding_section` serves both emitters, so one elision covers both."""
        message = route_mod.owner_message(routed([routed_row(reasoning=PATHY)]), COMPANION,
                                          destination="owner", workspace=self.root)
        self.assertIn(".../UnsignedMeasurability.lean:324", message)
        self.assertNotRegex(message, r"(?<![\w<])/(Users|home)/")

    def test_the_decision_note_states_the_basis_of_its_own_percentage(self):
        """FR#36: the figure travels into a drafted issue body with its n."""
        body = self.body(routed_row())
        self.assertIn("40% of the time (4 of 10 adjudicated rows", body)


# ==========================================================================
# FR#5 / FR#9 / FR#18 -- whose git runs, and where hydrated text may land
# ==========================================================================
def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), "-c", "core.fsmonitor=", *args],
                          capture_output=True, text=True, check=False)


def make_hostile_repo(root: Path) -> tuple[Path, Path]:
    """A git repo whose ``core.fsmonitor`` writes a marker.  Returns (repo, marker)."""
    repo = root / "hostile"
    (repo / "notes").mkdir(parents=True)
    marker = root / "PWNED-fsmonitor"
    hook = root / "fsmon.sh"
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n', encoding="utf-8")
    hook.chmod(0o755)
    (repo / ".gitignore").write_text("notes/\n", encoding="utf-8")
    (repo / "a.txt").write_text("hi\n", encoding="utf-8")
    _git(repo, "init", "-q", ".")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    _git(repo, "config", "core.fsmonitor", str(hook))
    marker.unlink(missing_ok=True)
    return repo, marker


class NoForeignRepoNamesTheProgramTest(unittest.TestCase):
    """`git check-ignore` refreshes the index, and that runs `core.fsmonitor`.

    `--out` names any path on the machine, so the repo answering the ignore
    question is not one this skill owns.  The safety check was the execve.
    """

    def setUp(self):
        if shutil.which("git") is None:                      # pragma: no cover
            self.skipTest("git is not installed")
        self.root = scratch(self)
        self.repo, self.marker = make_hostile_repo(self.root)
        self.target = self.repo / "notes" / "hydrated.adam.jsonl"

    def _the_unsafe_spelling_fires(self) -> bool:
        subprocess.run(["git", "-C", str(self.repo), "check-ignore", "-q", str(self.target)],
                       capture_output=True, text=True, check=False)
        fired = self.marker.exists()
        self.marker.unlink(missing_ok=True)
        return fired

    def test_tracked_by_git_runs_nothing_the_answering_repo_chose(self):
        unsafe_ran = self._the_unsafe_spelling_fires()
        quotes_mod._tracked_by_git(self.target)
        self.assertFalse(self.marker.exists(),
                         "the answering repo's core.fsmonitor ran out of `cf quotes hydrate`")
        if not unsafe_ran:                                   # pragma: no cover - git build
            self.skipTest("this git does not run core.fsmonitor on check-ignore")

    def test_check_ignore_runs_nothing_the_answering_repo_chose(self):
        unsafe_ran = self._the_unsafe_spelling_fires()
        init_mod.check_ignore(self.repo, self.target)
        self.assertFalse(self.marker.exists())
        if not unsafe_ran:                                   # pragma: no cover - git build
            self.skipTest("this git does not run core.fsmonitor on check-ignore")

    def test_the_pinned_config_does_not_change_the_answer(self):
        """A guard that changes a verdict is a bug, not a guard."""
        ignored = self.repo / "notes" / "x.jsonl"
        tracked = self.repo / "b.txt"
        self.assertTrue(init_mod.check_ignore(self.repo, ignored))
        self.assertFalse(init_mod.check_ignore(self.repo, tracked))
        self.assertEqual(init_mod.git_toplevel(self.repo / "notes"),
                         Path(_git(self.repo, "rev-parse", "--show-toplevel").stdout.strip()))


class HydratedTextNeverLandsInACompanionTest(unittest.TestCase):
    """CLAUDE.md S4: a companion tree is read as data, and nothing writes there.

    The git check could not be the guard: it refuses only a repo that does NOT
    ignore the path, so a companion that ignores it -- or that has no `.git` at
    all -- was written to, and the check itself ran the tree's own program first.
    """

    def setUp(self):
        if shutil.which("git") is None:                      # pragma: no cover
            self.skipTest("git is not installed")
        self.root = scratch(self)
        self.repo, self.marker = make_hostile_repo(self.root)
        self.workspace = make_workspace(self, companion_repo=self.repo)

    def test_an_out_inside_a_companion_repo_is_refused_by_name(self):
        out = self.repo / "notes" / "hydrated.adam.jsonl"
        with self.assertRaises(CfError) as ctx:
            quotes_mod._refuse_unsafe_out(out, self.workspace)
        self.assertIn(COMPANION, str(ctx.exception))
        self.assertIn(str(self.repo), str(ctx.exception))
        self.assertFalse(self.marker.exists(), "no git ran in the companion at all")
        self.assertFalse(out.exists())

    def test_a_companion_with_no_git_at_all_is_refused_too(self):
        """`git_toplevel` answers None for a plain directory, so nothing refused it."""
        plain = self.root / "plain-companion"
        plain.mkdir()
        workspace = make_workspace(self, companion_repo=plain)
        out = plain / "hydrated.adam.jsonl"
        with self.assertRaises(CfError) as ctx:
            quotes_mod._refuse_unsafe_out(out, workspace)
        self.assertIn("companion repo", str(ctx.exception))
        self.assertFalse(out.exists())

    def test_the_refusal_names_a_path_that_works(self):
        out = self.repo / "notes" / "hydrated.adam.jsonl"
        with self.assertRaises(CfError) as ctx:
            quotes_mod._refuse_unsafe_out(out, self.workspace)
        suggested = Path(str(ctx.exception.hint).split("--out ")[1].split(" ")[0])
        quotes_mod._refuse_unsafe_out(suggested, self.workspace)    # must not raise

    def test_the_command_writes_nothing_and_exits_two(self):
        out = self.repo / "notes" / "hydrated.adam.jsonl"
        write_jsonl(self.workspace.paths.routed_jsonl(COMPANION),
                    [routed_row(book_quote=redact_mod.redact_quote(SENTENCE, pages=[2]))],
                    meta={"repo": COMPANION, "status": "CHALLENGED", "schema": "routed-v2"})
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "cf.py"), "--workspace", str(self.workspace.root),
             "quotes", "hydrate", "--ledger", "--no-text", "--out", str(out)],
            capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("companion repo", res.stderr)
        self.assertFalse(out.exists())
        self.assertFalse(self.marker.exists())


class NoBareGitIsReachableFromAnOperatorPathTest(unittest.TestCase):
    """The static guard whose absence let this ship.

    The existing scan covered `scripts/detect/dump_deps.sh` only, so a
    `subprocess.run(["git", ...])` added to a command module was invisible to it.
    """

    #: the functions that may run plain `git`: each one is only ever asked about
    #: the workspace repository `cf init` created, never about a path an operator
    #: named.  `remotes` and `_config` say so in their own docstrings.  Exactly
    #: the four the scan reaches, and no more: a wider name re-opens the hole.
    #: `run` in particular is the operator-facing entry point of EVERY module
    #: under `scripts/cfcmd`, so allowing it allowed a bare
    #: `subprocess.run(["git", "-C", <operator path>, ...])` in any command.
    WORKSPACE_ONLY = {"remotes", "_config", "_install_no_remote_guard", "_init_git"}

    @staticmethod
    def carries_the_pins(argv: ast.List) -> bool:
        """Is ``*SAFE_CONFIG`` spliced into this argv literal?

        ``gitsafe.filter_names`` has to ask git which filter drivers a repo names
        before ``gitsafe.env`` can blank them, so it spells its one call out --
        with the pins in it.  That is a pinned call, not a bare one.
        """
        return any(isinstance(e, ast.Starred) and isinstance(e.value, ast.Name)
                   and e.value.id == "SAFE_CONFIG" for e in argv.elts)

    def bare_git_calls(self, path: Path) -> list[tuple[str, int]]:
        """``(enclosing function, line)`` for each ``subprocess.run(["git", ...])``."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: list[tuple[str, int]] = []

        def walk(node: ast.AST, where: str) -> None:
            for child in ast.iter_child_nodes(node):
                name = (child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                        else where)
                if isinstance(child, ast.Call) and not isinstance(child.func, ast.Name):
                    args = child.args or []
                    if (isinstance(child.func, ast.Attribute) and child.func.attr == "run"
                            and args and isinstance(args[0], ast.List) and args[0].elts
                            and isinstance(args[0].elts[0], ast.Constant)
                            and args[0].elts[0].value == "git"
                            and not self.carries_the_pins(args[0])):
                        found.append((where, child.lineno))
                walk(child, name)

        walk(tree, "<module>")
        return found

    def scan(self) -> list[tuple[str, str, int]]:
        """``(module, enclosing function, line)`` for every bare-git site scanned."""
        sites = []
        for directory in ("cfcmd", "cflib"):
            for path in sorted((SCRIPTS / directory).glob("*.py")):
                for where, line in self.bare_git_calls(path):
                    sites.append((f"{directory}/{path.name}", where, line))
        return sites

    def test_every_git_in_cfcmd_and_cflib_is_pinned_or_workspace_only(self):
        offenders = [f"{module}:{line} in {where}()"
                     for module, where, line in self.scan()
                     if where not in self.WORKSPACE_ONLY]
        self.assertEqual(offenders, [], "plain `git` reachable from an operator's path")

    def test_every_allowlist_entry_is_needed(self):
        """A stale exemption is how an allowlist becomes a second rulebook.

        Three of the seven names this set started with were dead -- `run` and
        `argv` (`gitsafe`'s own, whose argv is a call and never a list literal)
        and `_set_config`, which is in no module of `scripts/` -- and `run` is the
        name of every command's entry point, so the guard passed a planted
        `subprocess.run(["git", "-C", <operator path>, "status"])` in one.
        """
        reached = {where for _module, where, _line in self.scan()}
        self.assertEqual(sorted(self.WORKSPACE_ONLY), sorted(reached),
                         "the allowlist and the sites the scan reaches must be the same set")

    def test_the_hydrate_command_holds_no_git_of_its_own(self):
        source = (SCRIPTS / "cfcmd" / "quotes.py").read_text(encoding="utf-8")
        self.assertIn("from cflib import gitsafe", source)
        self.assertNotIn("from cfcmd.init import", source)
        self.assertNotIn('"git", "-C"', source)

    def test_the_two_helpers_the_out_path_reaches_use_the_safe_runner(self):
        import inspect

        for helper in (init_mod.check_ignore, init_mod.git_toplevel):
            source = inspect.getsource(helper)
            self.assertIn("gitsafe.run", source, helper.__name__)
            self.assertNotIn('"git", "-C"', source, helper.__name__)


# ==========================================================================
# FR#28 -- the export named a next step that refuses the bundle it just wrote
# ==========================================================================
class TheNextStepAnExportPrintsSucceedsTest(unittest.TestCase):
    """`cf share import` refuses a bundle with no accepted/refuted rows.

    That is every findings-only campaign, i.e. every campaign before an
    adjudication has run -- and the export printed the import as the next step
    two lines under a table reading `ledger rows 0`.
    """

    def export(self, *, challenged: bool) -> tuple[dict, Path, str]:
        import argparse

        from cfcmd import share as share_cmd

        workspace = make_workspace(self)
        status = "CHALLENGED" if challenged else "UNCHALLENGED"
        write_jsonl(workspace.paths.routed_jsonl(COMPANION),
                    [routed_row(challenged=challenged)],
                    meta={"repo": COMPANION, "status": status, "schema": "routed-v2"})
        out = scratch(self) / "bundle"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            share_cmd.run(argparse.Namespace(
                share_command="export", workspace=str(workspace.root), out=str(out),
                companion=[], redact=None, include_statements=False,
                skip_unchallenged=True, force=False))
        return bundle_mod.read_manifest(out), out, buf.getvalue()

    def test_a_zero_ledger_export_does_not_name_the_import(self):
        for challenged in (True, False):
            with self.subTest(challenged=challenged):
                manifest, out, printed = self.export(challenged=challenged)
                self.assertEqual(manifest["counts"]["ledger_rows"], 0)
                nxt = printed.split("next:")[-1]
                # Backticks off before the check: `cf share import` in prose names
                # the same command a bare one does, and the check that kept them
                # let any backticked line past without ever looking at it.
                offered = [line.strip().replace("`", "") for line in nxt.splitlines()
                           if line.strip()]
                named = [c for c in offered if c.startswith("cf share import")]
                self.assertEqual([c for c in named if "refuses this bundle" not in c], [],
                                 "the export named a command that refuses its own bundle")
                self.assertEqual([c for c in named if str(out) in c], [],
                                 "a line that names the bundle is an invitation to run it")
                self.assertIn(f"cf adjudicate --companion {COMPANION}", nxt)
                if manifest["counts"]["routed_rows"]:
                    self.assertIn("cf quotes hydrate --bundle", nxt)

    def test_the_refusal_the_import_makes_is_the_one_the_export_states(self):
        """This workspace has never adjudicated, so the bundle has no ledger FILE.

        The export says so in the same words the import refuses in; the sibling
        below runs the import and gets the refusal.
        """
        manifest, out, printed = self.export(challenged=True)
        self.assertEqual(bundle_mod.manifest_ledger_files(manifest), [])
        nxt = printed.split("next:")[-1]
        self.assertIn("`cf share import` refuses this bundle", nxt)
        self.assertIn("cf quotes hydrate", nxt)

    def test_the_refusal_it_would_have_walked_into_is_still_the_refusal(self):
        manifest, out, _ = self.export(challenged=True)
        other = make_workspace(self)
        with self.assertRaises(CfError) as ctx:
            merge_mod.merge_bundle(other, out, manifest)
        self.assertIn("no ledger rows", str(ctx.exception))

    def test_an_export_with_ledger_rows_still_names_the_import(self):
        import argparse

        from cfcmd import share as share_cmd

        workspace = make_workspace(self)
        write_jsonl(workspace.paths.accepted_jsonl(COMPANION), [{
            "finding_id": "c3" * 32, "item": "Exercise 1.2.3", "repo": COMPANION,
            "kind": "CONTENT", "verdict": "CONFIRMED_CONTENT", "severity": "low",
            "reason": "the statement drops a hypothesis", "method": "workflow:adjudicate",
            "source_id": SOURCE_ID, "source_sha": SOURCE_SHA,
        }])
        out = scratch(self) / "bundle"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            share_cmd.run(argparse.Namespace(
                share_command="export", workspace=str(workspace.root), out=str(out),
                companion=[], redact=None, include_statements=False,
                skip_unchallenged=True, force=False))
        self.assertIn("cf share import", buf.getvalue().split("next:")[-1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
