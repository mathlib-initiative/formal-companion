"""Regression tests for sharing a campaign: `cf share` and `cf quotes hydrate`.

The two findings this file closes:

* **critic#3** -- the system of record was a git repo forbidden to have a remote,
  so a campaign could not be shared and two ledgers could not be merged.  There
  was no merge, import-ledger or sync command at all, and `cf route
  --emit-upstream` only ever wrote one companion's REPORTABLE rows to a repo
  owner.  The answer is an export/import pair whose bundle carries the
  conclusions and no book text, plus a hydration that puts the text back for
  somebody who holds the same edition.
* **security-safety#5 (the code half)** -- the pre-push hook is a reminder and
  nothing said so in the places an operator meets it; `cf init --no-remote-guard`
  is the stronger local-config guard a bare `git push --no-verify` does not get
  past, and `cf status` reports whether it is set.

The load-bearing test is :meth:`BundleCarriesNoBookTextTest.
test_no_sentence_of_the_source_appears_anywhere_in_the_bundle`: it greps every
byte of a bundle for the pinned text's own sentences.  Everything else is a rule
about what a reader may conclude from a bundle.

Stdlib unittest only, no network, nothing written outside a temp directory and
nothing read from a companion repository.
"""
from __future__ import annotations

# The first import, before anything reads HOME: it pins HOME at a temporary
# directory for `unittest discover`, which loads no conftest.py.  The second
# spelling is for `python3 -m unittest tests.<module>`.  See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
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
from share import bundle as bundle_mod  # noqa: E402
from share import hydrate as hydrate_mod  # noqa: E402
from share import merge as merge_mod  # noqa: E402
from share import redact as redact_mod  # noqa: E402

PUBLISHED = "smith-analysis-2e"
DRAFT = "smith-analysis-1e"
SHA_PUBLISHED = "a1" * 32
SHA_DRAFT = "b2" * 32

#: a synthetic three-page source: form feeds are the only page boundary a
#: workspace has, and the quote the findings cite sits on page 2
PAGE_ONE = "Chapter 1. Elementary measure\n\nWe begin with the elementary theory.\n"
QUOTE = ("Show that the elementary measure m(E) is finite and non-negative, and that it "
         "does not depend on the choice of partition.")
OTHER_QUOTE = "Prove that the union of two elementary sets is elementary."
PAGE_TWO = (
    "Exercise 1.1.1. Let E be an elementary set.\n"
    f"{QUOTE}\n"
    "\n"
    "Exercise 1.1.2. Let F be a second elementary set.\n"
)
PAGE_THREE = f"Exercise 1.1.3. {OTHER_QUOTE}\n"
SOURCE_TEXT = "\f".join([PAGE_ONE, PAGE_TWO, PAGE_THREE])
#: the same text as a pdftotext dump that wrapped the sentence mid-line
WRAPPED_TEXT = SOURCE_TEXT.replace("non-negative, and that it", "non-negative,\nand that it")
QUOTE_PAGE = 2
#: a run of the source long enough to be redacted, written INSIDE a prose field --
#: which is what a worker explaining a finding actually does
LIFTED = "the elementary measure m(E) is finite and non-negative"
#: and one that is not long enough, so the threshold is a threshold
SHORT_LIFT = "elementary set"


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path.resolve()   # /var -> /private/var on macOS: cflib resolves its paths


def workspace_yaml(*, name: str, repo: Path, published_sha: str = SHA_PUBLISHED,
                   companion: str = "adam") -> str:
    return (
        "version: 2\n"
        f"workspace:\n  name: {name}\n"
        "companions:\n"
        f"  {companion}:\n"
        f"    repo: {repo}\n"
        "    adapter: docstring_tags\n"
        f"    sources: [{PUBLISHED}]\n"
        f"    anchor_source: {PUBLISHED}\n"
        "sources:\n"
        f"  {PUBLISHED}:\n"
        f'    sha256: "{published_sha}"\n'
        "    title: An Introduction to Analysis\n"
        '    edition: "second edition (published pagination)"\n'
        "    offset: 16\n"
        "    role: audited\n"
        "hosts:\n  allowlist: []\n"
    )


def ledger_row(**over) -> dict:
    row = {
        "finding_id": "c3" * 32,
        "item": "Exercise 1.1.1",
        "repo": "adam",
        "kind": "CONTENT",
        "unfaithful_decls": ["adam:Elementary.measure_nonneg"],
        "verdict": "CONFIRMED_CONTENT",
        "severity": "low",
        "confidence": 0.9,
        "contested": False,
        "dissent": [],
        "reason": "the Lean statement drops the finiteness half",
        "method": "workflow:adjudicate",
        "source_id": PUBLISHED,
        "source_sha": SHA_PUBLISHED,
    }
    row.update(over)
    return row


def routed_row(**over) -> dict:
    row = {
        "finding_id": "c3" * 32,
        "run_id": "run-1",
        "source_id": PUBLISHED,
        "source_sha": SHA_PUBLISHED,
        "item": "Exercise 1.1.1",
        "kind": "CONTENT",
        "severity": "low",
        "repo": "adam",
        "unfaithful_decls": ["adam:Elementary.measure_nonneg"],
        "other_decls": [],
        "faithful": [],
        "concept": "finiteness of elementary measure",
        "book_quote": QUOTE,
        "reasoning": "the statement admits an infinite measure",
        "suggested_fix": "add the finiteness conclusion",
        "provenance": "worker",
        "source_file": "corpus/run-1/exercise-1-1-1.json",
        "challenged": True,
        "ledger_verdict": "CONFIRMED_CONTENT",
    }
    row.update(over)
    return row


def make_workspace(case: unittest.TestCase, *, name: str = "alice",
                   published_sha: str = SHA_PUBLISHED, text: str | None = SOURCE_TEXT,
                   ledger: bool = True, routed: bool = True) -> Workspace:
    """A workspace with one companion, one source and a small campaign in it."""
    root = scratch(case) / "ws"
    repo = root.parent / "companion-repo"
    (repo / "Analysis").mkdir(parents=True)
    root.mkdir(parents=True)
    (root / "workspace.yaml").write_text(
        workspace_yaml(name=name, repo=repo, published_sha=published_sha), encoding="utf-8")
    workspace = Workspace.load(root)
    paths = workspace.paths
    paths.ensure_skeleton()
    paths.companion_dir("adam").mkdir(parents=True, exist_ok=True)
    paths.adapter_yaml("adam").write_text("kind: docstring_tags\n", encoding="utf-8")
    write_jsonl(paths.statements_jsonl("adam"),
                [{"declaration": "adam:Elementary.measure_nonneg", "item": "Exercise 1.1.1",
                  "statement": "theorem measure_nonneg : 0 <= m E"}])
    if text is not None:
        paths.source_dir(PUBLISHED).mkdir(parents=True, exist_ok=True)
        paths.source_text(PUBLISHED).write_text(text, encoding="utf-8")
    if ledger:
        write_jsonl(paths.accepted_jsonl("adam"), [ledger_row()])
    if routed:
        write_jsonl(paths.routed_jsonl("adam"), [routed_row()],
                    meta={"repo": "adam", "status": "CHALLENGED", "schema": "routed-v2"})
    write_json(paths.tally_json("run-1"), {"rows": 1, "items": 1, "high": 0})
    write_json(paths.receipt_json("run-1"), {
        "run_id": "run-1", "complete": True,
        "destination": str(paths.corpus_run_dir("run-1")),
        "source": {"kind": "harvest", "host": "examplebox", "path": "/srv/audit/run-1",
                   "preset": "differential"},
        "sources": {PUBLISHED: SHA_PUBLISHED},
        "expected": {"count": 1, "names": ["exercise-1-1-1.json"]},
        "actual": {"count": 1},
    })
    return workspace


def walk_strings(value, path: str = ""):
    """``(json path, string)`` for every string in *value*, at any depth.

    A prose check that iterates a list of field names can only find what that
    list already knows about, which is how five free-text keys of a v2 ledger row
    travelled verbatim past a scrubber and a test that both read `PROSE_FIELDS`.
    """
    if isinstance(value, str):
        if value.strip():
            yield path, value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from walk_strings(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for n, inner in enumerate(value):
            yield from walk_strings(inner, f"{path}[{n}]")


def bundle_files(bundle: Path) -> dict[str, str]:
    """Every file in a bundle directory, as text keyed by its relative path."""
    return {p.relative_to(bundle).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(bundle.rglob("*")) if p.is_file()}


def export(case: unittest.TestCase, workspace: Workspace, **kw) -> tuple[dict, Path]:
    out = Path(kw.pop("out", None) or (scratch(case) / "bundle"))
    manifest = bundle_mod.export_bundle(workspace, out, **kw)
    return manifest, out


@contextlib.contextmanager
def captured_warnings():
    """The lines `cflib.log.warn` wrote, as a list, while the block ran.

    The level is pinned and restored: it is module state another test may have
    turned down, and a warning that is not printed is not what is under test here.
    """
    from cflib import log

    was = log.level()
    log.set_level(1)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            lines: list[str] = []
            yield lines
    finally:
        log.set_level(was)
    lines.extend(buf.getvalue().splitlines())


# ==========================================================================
# critic#3 -- the bundle: conclusions travel, the book does not
# ==========================================================================
class BundleCarriesNoBookTextTest(unittest.TestCase):
    def setUp(self):
        self.workspace = make_workspace(self)

    def test_no_sentence_of_the_source_appears_anywhere_in_the_bundle(self):
        """The whole point of the command, tested by grepping every byte."""
        _, out = export(self, self.workspace)
        blob = "\n".join(bundle_files(out).values())
        for sentence in (QUOTE, OTHER_QUOTE, "We begin with the elementary theory."):
            self.assertNotIn(sentence, blob)
        # nor the lowercase/collapsed form the digest is taken over
        self.assertNotIn(redact_mod.normalise_quote(QUOTE), blob.lower())

    def test_the_manifest_lists_every_file_with_its_sha256(self):
        manifest, out = export(self, self.workspace)
        listed = {entry["path"] for entry in manifest["files"]}
        on_disk = set(bundle_files(out)) - {bundle_mod.MANIFEST_NAME}
        self.assertEqual(listed, on_disk)
        self.assertEqual(bundle_mod.verify_bundle(out, manifest), [])
        self.assertEqual(manifest["quotes"], "redacted")
        self.assertEqual(manifest["redaction"], redact_mod.REDACTION)

    def test_the_manifest_says_what_it_withheld(self):
        manifest, _ = export(self, self.workspace)
        withheld = " ".join(manifest["withheld"]).lower()
        for expected in ("text.txt", "page images", "pdf", "corpus"):
            self.assertIn(expected, withheld)

    def test_a_redacted_quote_carries_page_length_hash_and_edges(self):
        _, out = export(self, self.workspace)
        rows = [r for r in read_jsonl(out / "ledger" / "routed" / "findings.adam.jsonl")
                if "_meta" not in r]
        record = rows[0]["book_quote"]
        self.assertTrue(record["redacted"])
        self.assertEqual(record["page"], QUOTE_PAGE)
        self.assertEqual(record["norm_chars"], len(redact_mod.normalise_quote(QUOTE)))
        self.assertEqual(record["sha256"], redact_mod.quote_digest(QUOTE))
        words = redact_mod.normalise_quote(QUOTE).split()
        self.assertEqual(record["first_words"],
                         " ".join(words[:redact_mod.edge_words(words)]))

    def test_the_digest_is_the_one_route_already_computed(self):
        """`cf route --redact-quotes` and `cf share export` must agree on a quote.

        Two spellings of "the normalised quote" would make the same sentence two
        different things depending on which command shared it.
        """
        from route import route as route_mod

        theirs = route_mod._redact({"book_quote": QUOTE})["book_quote"]["sha256"]
        self.assertEqual(theirs, redact_mod.quote_digest(QUOTE))

    def test_full_quotes_is_recorded_as_verbatim_and_really_carries_them(self):
        manifest, out = export(self, self.workspace, redact=False)
        self.assertEqual(manifest["quotes"], "VERBATIM")
        self.assertIn(QUOTE, "\n".join(bundle_files(out).values()))

    def test_statements_stay_behind_unless_asked_for(self):
        _, out = export(self, self.workspace)
        self.assertNotIn("companions/adam/statements.jsonl", bundle_files(out))
        _, out2 = export(self, self.workspace, include_statements=True)
        self.assertIn("companions/adam/statements.jsonl", bundle_files(out2))

    def test_a_companion_with_no_routed_findings_still_shares_what_it_has(self):
        """The rest of a companion must not be skipped because `cf route` has not run."""
        self.workspace.paths.routed_jsonl("adam").unlink()
        manifest, out = export(self, self.workspace, include_statements=True)
        files = bundle_files(out)
        self.assertIn("companions/adam/statements.jsonl", files)
        self.assertIn("ledger/accepted.adam.jsonl", files)
        self.assertEqual(manifest["counts"]["routed_rows"], 0)

    def test_a_receipt_travels_without_the_exporters_machine(self):
        _, out = export(self, self.workspace)
        receipt = json.loads((out / "corpus" / "run-1" / "RECEIPT.json")
                             .read_text(encoding="utf-8"))
        self.assertNotIn("destination", receipt)
        self.assertNotIn("host", receipt["source"])
        self.assertNotIn("path", receipt["source"])
        self.assertEqual(receipt["source"]["preset"], "differential")
        self.assertTrue(receipt["complete"])
        self.assertNotIn("examplebox", json.dumps(receipt))

    def test_source_metadata_is_facts_about_the_edition_only(self):
        _, out = export(self, self.workspace)
        sources = json.loads((out / bundle_mod.SOURCES_NAME).read_text(encoding="utf-8"))
        entry = sources["sources"][PUBLISHED]
        self.assertEqual(entry["sha256"], SHA_PUBLISHED)
        self.assertEqual(entry["offset"], 16)
        self.assertNotIn("pdf", entry)
        self.assertNotIn("repo", sources["companions"]["adam"])
        self.assertEqual(sources["companions"]["adam"]["anchor_source"], PUBLISHED)

    def test_a_tarball_and_a_directory_carry_the_same_files(self):
        manifest_dir, out_dir = export(self, self.workspace)
        tar_path = scratch(self) / "bundle.tar.gz"
        manifest_tar, _ = export(self, self.workspace, out=tar_path)
        self.assertEqual([f["path"] for f in manifest_dir["files"]],
                         [f["path"] for f in manifest_tar["files"]])
        with tarfile.open(tar_path) as tar:
            self.assertEqual(sorted(m.name for m in tar.getmembers() if m.isfile()),
                             sorted(bundle_files(out_dir)))

    def test_an_unknown_companion_is_refused_by_name(self):
        with self.assertRaises(CfError) as caught:
            export(self, self.workspace, companions=["nobody"])
        self.assertIn("nobody", str(caught.exception))


class BundleDestinationTest(unittest.TestCase):
    """A bundle must not be written into a repo the no-remote rule is about."""

    def setUp(self):
        self.workspace = make_workspace(self)

    def _refused(self, out: Path) -> str:
        with self.assertRaises(CfError) as caught:
            bundle_mod.export_bundle(self.workspace, out)
        return str(caught.exception)

    def test_inside_the_workspace_is_refused(self):
        self.assertIn("workspace directory",
                      self._refused(self.workspace.root / "share" / "bundle"))

    def test_inside_a_companion_repo_is_refused(self):
        repo = self.workspace.companions["adam"].repo
        self.assertIn("companion repo", self._refused(repo / "bundle"))

    def test_inside_the_skill_repo_is_refused(self):
        self.assertIn("skill repo", self._refused(REPO / "bundle"))

    def test_inside_the_git_repo_holding_the_workspace_is_refused(self):
        outer = self.workspace.root.parent
        subprocess.run(["git", "init", "-q", str(outer)], check=True, capture_output=True)
        self.addCleanup(shutil.rmtree, outer / ".git", ignore_errors=True)
        self.assertIn("git repo holding this workspace", self._refused(outer / "bundle"))

    def test_an_existing_bundle_is_not_overwritten_without_force(self):
        out = scratch(self) / "bundle"
        bundle_mod.export_bundle(self.workspace, out)
        with self.assertRaises(CfError) as caught:
            bundle_mod.export_bundle(self.workspace, out)
        self.assertIn("not empty", str(caught.exception))
        bundle_mod.export_bundle(self.workspace, out, force=True)

    def test_the_predicate_quotes_hydrate_checks_is_the_export_set(self):
        """One predicate, so `--out` cannot drift from what a bundle carries."""
        _, out = export(self, self.workspace, include_statements=True)
        for relative in bundle_files(out):
            self.assertTrue(bundle_mod.would_export(relative), relative)
        for relative in ("quotes/hydrated.jsonl", "sources/smith/text.txt",
                         "reports/union.adam.json"):
            self.assertFalse(bundle_mod.would_export(relative), relative)


# ==========================================================================
# critic#3 -- import: two ledgers merge, and no verdict is lost
# ==========================================================================
class ImportMergesLedgersTest(unittest.TestCase):
    def setUp(self):
        self.alice = make_workspace(self, name="alice")
        self.bob = make_workspace(self, name="bob", ledger=False)
        _, self.bundle = export(self, self.alice)
        self.manifest = bundle_mod.read_manifest(self.bundle)

    def _accepted(self, workspace: Workspace) -> list[dict]:
        path = workspace.paths.accepted_jsonl("adam")
        return [r for r in read_jsonl(path) if "_meta" not in r] if path.exists() else []

    def test_a_row_arrives_stamped_with_where_it_came_from(self):
        report = merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertEqual(report["added"], 1)
        row = self._accepted(self.bob)[0]
        self.assertEqual(row["verdict"], "CONFIRMED_CONTENT")
        self.assertEqual(row[merge_mod.SHARED_FROM], "alice")
        self.assertEqual(row[merge_mod.SHARED_BUNDLE], self.manifest["bundle_id"][:12])

    def test_importing_twice_changes_nothing(self):
        merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        path = self.bob.paths.accepted_jsonl("adam")
        first = path.read_bytes()
        report = merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertEqual((report["added"], report["unchanged"]), (0, 1))
        self.assertEqual(path.read_bytes(), first)

    def test_the_same_verdict_already_here_is_a_no_op(self):
        write_jsonl(self.bob.paths.accepted_jsonl("adam"), [ledger_row()])
        report = merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertEqual((report["added"], report["unchanged"]), (0, 1))
        self.assertEqual(len(self._accepted(self.bob)), 1)

    def test_two_verdicts_for_one_finding_are_both_kept_and_marked(self):
        """A disagreement between two adjudicators is the row that matters most."""
        write_jsonl(self.bob.paths.accepted_jsonl("adam"),
                    [ledger_row(verdict="UNSURE", reason="cannot tell from the statement")])
        report = merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertEqual(report["added"], 1)
        rows = self._accepted(self.bob)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["verdict"] for r in rows}, {"UNSURE", "CONFIRMED_CONTENT"})
        for row in rows:
            self.assertTrue(row["conflict"])
            self.assertEqual(row["conflict_verdicts"], ["CONFIRMED_CONTENT", "UNSURE"])
        self.assertEqual(report["conflicts"][0]["joined_by"], "finding_id")

    def test_a_conflict_import_is_also_idempotent(self):
        write_jsonl(self.bob.paths.accepted_jsonl("adam"), [ledger_row(verdict="UNSURE")])
        merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        first = self.bob.paths.accepted_jsonl("adam").read_bytes()
        merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertEqual(self.bob.paths.accepted_jsonl("adam").read_bytes(), first)

    def test_the_same_defect_from_another_run_joins_by_defect_key(self):
        """`run_id` is the first part of a finding id, so two runs of one defect
        have two ids and only the run-independent key links them."""
        other_id = "d4" * 32
        write_jsonl(self.bob.paths.accepted_jsonl("adam"),
                    [ledger_row(finding_id=other_id, verdict="UNSURE")])
        report = merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertEqual(report["conflicts"][0]["joined_by"], "defect_key")
        self.assertEqual(len(self._accepted(self.bob)), 2)

    def test_a_source_sha_mismatch_refuses_and_names_both_shas(self):
        theirs = make_workspace(self, name="carol", published_sha=SHA_DRAFT, ledger=False)
        with self.assertRaises(CfError) as caught:
            merge_mod.merge_bundle(theirs, self.bundle, self.manifest)
        message = str(caught.exception)
        self.assertIn(SHA_PUBLISHED, message)
        self.assertIn(SHA_DRAFT, message)
        self.assertEqual(self._accepted(theirs), [])

    def test_a_source_this_workspace_never_pinned_is_refused(self):
        rows = [ledger_row(source_id="somebody-elses-book", source_sha="e5" * 32)]
        write_jsonl(self.bundle / "ledger" / "accepted.adam.jsonl", rows)
        manifest = dict(self.manifest, sources={"somebody-elses-book": "e5" * 32})
        with self.assertRaises(CfError) as caught:
            merge_mod.merge_bundle(self.bob, self.bundle, manifest)
        self.assertIn("somebody-elses-book", str(caught.exception))
        self.assertIn("cf sources add", caught.exception.hint or "")

    def test_a_row_that_names_no_edition_is_refused(self):
        write_jsonl(self.bundle / "ledger" / "accepted.adam.jsonl", [ledger_row(source_sha="")])
        manifest = dict(self.manifest, sources={PUBLISHED: ""})
        with self.assertRaises(CfError) as caught:
            merge_mod.merge_bundle(self.bob, self.bundle, manifest)
        self.assertIn("which edition", str(caught.exception))

    def test_a_verdict_outside_the_vocabulary_is_refused(self):
        write_jsonl(self.bundle / "ledger" / "accepted.adam.jsonl",
                    [ledger_row(verdict="LOOKS_FINE_TO_ME")])
        with self.assertRaises(CfError) as caught:
            merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertIn("LOOKS_FINE_TO_ME", str(caught.exception))

    def test_dry_run_writes_nothing(self):
        report = merge_mod.merge_bundle(self.bob, self.bundle, self.manifest, dry_run=True)
        self.assertEqual(report["added"], 1)
        self.assertFalse(self.bob.paths.accepted_jsonl("adam").exists())


class BundleVerificationTest(unittest.TestCase):
    def setUp(self):
        self.workspace = make_workspace(self)
        _, self.bundle = export(self, self.workspace)
        self.manifest = bundle_mod.read_manifest(self.bundle)

    def test_an_altered_file_is_named(self):
        path = self.bundle / "ledger" / "accepted.adam.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace("CONFIRMED_CONTENT", "REFUTED"),
                        encoding="utf-8")
        problems = bundle_mod.verify_bundle(self.bundle, self.manifest)
        self.assertEqual(len(problems), 1)
        self.assertIn("altered: ledger/accepted.adam.jsonl", problems[0])

    def test_a_missing_file_is_named(self):
        (self.bundle / "ledger" / "accepted.adam.jsonl").unlink()
        self.assertIn("missing: ledger/accepted.adam.jsonl",
                      bundle_mod.verify_bundle(self.bundle, self.manifest))

    def test_a_file_nobody_vouched_for_is_named(self):
        (self.bundle / "ledger" / "accepted.smuggled.jsonl").write_text("{}\n", encoding="utf-8")
        self.assertIn("not in the manifest: ledger/accepted.smuggled.jsonl",
                      bundle_mod.verify_bundle(self.bundle, self.manifest))

    def test_a_manifest_path_climbing_out_of_a_directory_bundle_is_refused(self):
        """A bundle handed over as a directory is input too: its manifest is data."""
        manifest = dict(self.manifest,
                        files=[{"path": "../../evil.json", "sha256": "0" * 64, "bytes": 2}])
        with self.assertRaises(CfError) as caught:
            bundle_mod.verify_bundle(self.bundle, manifest)
        self.assertIn("climbs out", str(caught.exception))

    def test_a_bundle_with_no_manifest_is_refused(self):
        (self.bundle / bundle_mod.MANIFEST_NAME).unlink()
        with self.assertRaises(CfError) as caught:
            bundle_mod.read_manifest(self.bundle)
        self.assertIn(bundle_mod.MANIFEST_NAME, str(caught.exception))

    def test_another_schema_is_refused(self):
        path = self.bundle / bundle_mod.MANIFEST_NAME
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema"] = "cf-share-v99"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            bundle_mod.read_manifest(self.bundle)
        self.assertIn("cf-share-v99", str(caught.exception))


class BundleMemberPathsTest(unittest.TestCase):
    """A bundle arrives from somebody else: its member names are input."""

    def _tar(self, name: str, *, kind: str = "file") -> Path:
        path = scratch(self) / "hostile.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            if kind == "file":
                data = b"{}\n"
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
        return path

    def _refused(self, archive: Path) -> str:
        with self.assertRaises(CfError) as caught:
            bundle_mod.extract_bundle(archive, scratch(self) / "out")
        return str(caught.exception)

    def test_a_path_climbing_out_of_the_bundle_is_refused(self):
        self.assertIn("climbs out", self._refused(self._tar("../evil.json")))

    def test_a_nested_climb_is_refused(self):
        self.assertIn("climbs out", self._refused(self._tar("ledger/../../evil.json")))

    def test_an_absolute_path_is_refused(self):
        self.assertIn("absolute path", self._refused(self._tar("/tmp/evil.json")))

    def test_a_windows_style_absolute_path_is_refused(self):
        self.assertIn("absolute path", self._refused(self._tar("C:\\evil.json")))

    def test_a_symlink_member_is_refused(self):
        self.assertIn("not a regular file", self._refused(self._tar("link", kind="symlink")))

    def test_nothing_was_written_before_the_refusal(self):
        out = scratch(self) / "out"
        with self.assertRaises(CfError):
            bundle_mod.extract_bundle(self._tar("../evil.json"), out)
        self.assertEqual(list(out.parent.rglob("evil.json")), [])


# ==========================================================================
# critic#3 -- hydration: the text comes back from the reader's own source
# ==========================================================================
class HydrateTest(unittest.TestCase):
    def setUp(self):
        self.workspace = make_workspace(self)
        self.record = redact_mod.redact_quote(QUOTE, pages=[QUOTE_PAGE])

    def test_the_recorded_hash_recovers_the_sentence_verbatim(self):
        result = hydrate_mod.hydrate_record(self.record, SOURCE_TEXT)
        self.assertEqual(result["status"], hydrate_mod.HYDRATED)
        self.assertEqual(result["text"], QUOTE)
        self.assertEqual(result["page"], QUOTE_PAGE)
        self.assertTrue(hydrate_mod.verify_text(self.record, result["text"]))

    def test_only_the_window_around_the_recorded_page_is_searched(self):
        result = hydrate_mod.hydrate_record(self.record, SOURCE_TEXT)
        self.assertEqual(result["searched_pages"],
                         [1, 2, 3])          # 2 +- PAGE_BIND_WINDOW, clipped to the source
        far = dict(self.record, page=40, pages=[40])
        self.assertEqual(hydrate_mod.hydrate_record(far, SOURCE_TEXT)["status"],
                         hydrate_mod.NOT_FOUND)

    def test_a_record_with_no_page_searches_every_page(self):
        record = redact_mod.redact_quote(QUOTE)
        self.assertIsNone(record["page"])
        result = hydrate_mod.hydrate_record(record, SOURCE_TEXT)
        self.assertEqual(result["status"], hydrate_mod.HYDRATED)
        self.assertEqual(result["text"], QUOTE)

    def test_the_holders_own_spelling_is_what_comes_back(self):
        """A pdftotext dump wraps lines where the worker's quote did not.

        The record is a hash of the NORMALISED sentence, so the wrapped text still
        matches and what is recovered is the reader's own bytes -- which is the
        only spelling they can check against their own page.
        """
        result = hydrate_mod.hydrate_record(self.record, WRAPPED_TEXT)
        self.assertEqual(result["status"], hydrate_mod.HYDRATED)
        self.assertIn("\n", result["text"])
        self.assertNotEqual(result["text"], QUOTE)
        self.assertEqual(redact_mod.normalise_quote(result["text"]),
                         redact_mod.normalise_quote(QUOTE))
        self.assertTrue(hydrate_mod.verify_text(self.record, result["text"]))

    def test_another_edition_says_not_found_rather_than_guessing(self):
        other = SOURCE_TEXT.replace("finite and non-negative", "finite")
        result = hydrate_mod.hydrate_record(self.record, other)
        self.assertEqual(result["status"], hydrate_mod.NOT_FOUND)
        self.assertEqual(result["text"], "")
        self.assertIn("not the edition", result["why"])

    def test_two_spellings_of_one_sentence_are_ambiguous_not_a_coin_flip(self):
        doubled = SOURCE_TEXT.replace(QUOTE, QUOTE + "\n" + QUOTE.replace(" is ", "  is  "))
        result = hydrate_mod.hydrate_record(self.record, doubled)
        self.assertEqual(result["status"], hydrate_mod.AMBIGUOUS)
        self.assertEqual(result["text"], "")
        self.assertIn("cannot be decided mechanically", result["why"])

    def test_a_flipped_operator_is_not_hydrated_as_the_book_sentence(self):
        """The digest keeps the operators, because that is the defect class."""
        flipped = QUOTE.replace("non-negative", "negative")
        record = redact_mod.redact_quote(flipped, pages=[QUOTE_PAGE])
        self.assertEqual(hydrate_mod.hydrate_record(record, SOURCE_TEXT)["status"],
                         hydrate_mod.NOT_FOUND)

    def test_a_route_redacted_record_hydrates_too(self):
        """``route._redact`` stores only the raw length; the search covers it."""
        from route import route as route_mod

        record = route_mod._redact({"book_quote": QUOTE})["book_quote"]
        self.assertNotIn("norm_chars", record)
        result = hydrate_mod.hydrate_record(record, SOURCE_TEXT)
        self.assertEqual(result["status"], hydrate_mod.HYDRATED)
        self.assertEqual(result["text"], QUOTE)

    def test_rows_without_a_pinned_text_are_counted_separately(self):
        rows = [routed_row(book_quote=self.record)]
        _, counts = hydrate_mod.hydrate_rows(rows, {})
        self.assertEqual(counts["no-source"], 1)
        self.assertEqual(counts[hydrate_mod.HYDRATED], 0)

    def test_a_verbatim_row_is_left_alone(self):
        reports, counts = hydrate_mod.hydrate_rows([routed_row()], {PUBLISHED: SOURCE_TEXT})
        self.assertEqual(reports, [])
        self.assertEqual(counts["verbatim"], 1)

    def test_the_short_quote_keeps_its_edges_to_itself(self):
        short = "Let E be an elementary set of finite measure."
        record = redact_mod.redact_quote(short)
        self.assertNotIn("first_words", record)
        self.assertIn("would be the whole quote", record["edges_withheld"])

    def test_a_long_quote_carries_its_edge_budget_from_each_end(self):
        words = redact_mod.normalise_quote(QUOTE).split()
        keep = redact_mod.edge_words(words)
        record = redact_mod.redact_quote(QUOTE)
        self.assertEqual(record["first_words"], " ".join(words[:keep]))
        self.assertEqual(record["last_words"], " ".join(words[-keep:]))
        self.assertLessEqual(keep, redact_mod.EDGE_WORDS)
        self.assertLess(2 * keep, len(words), "the edges are never most of the quote")

    def test_the_window_is_the_gates_own_page_bind_window(self):
        """The behaviour, not the import: a window hard-coded in the search would
        pass `hydrate.PAGE_BIND_WINDOW is PAGE_BIND_WINDOW` and fail this."""
        from gate.verify_findings import PAGE_BIND_WINDOW

        self.assertEqual(hydrate_mod.pages_to_search({"page": 10}, 100),
                         list(range(10 - PAGE_BIND_WINDOW, 10 + PAGE_BIND_WINDOW + 1)))


# ==========================================================================
# the two commands, end to end
# ==========================================================================
class ShareCommandTest(unittest.TestCase):
    def _cf(self, *args) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("CF_WORKSPACE", None)
        return subprocess.run([sys.executable, str(SCRIPTS / "cf.py"), *args],
                              capture_output=True, text=True, env=env, cwd=str(REPO))

    def setUp(self):
        self.alice = make_workspace(self, name="alice")
        self.bob = make_workspace(self, name="bob", ledger=False)
        self.out = scratch(self) / "share.tar.gz"

    def test_export_then_import_then_hydrate(self):
        res = self._cf("--workspace", str(self.alice.root), "share", "export",
                       "--out", str(self.out))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("redacted", res.stdout)
        self.assertTrue(self.out.exists())

        res = self._cf("--workspace", str(self.bob.root), "share", "import", str(self.out))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("verified", res.stdout)
        self.assertIn("merged 1 row(s)", res.stdout)

        res = self._cf("--workspace", str(self.bob.root), "quotes", "hydrate",
                       "--bundle", str(self.out))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("hydrated 1", res.stdout)
        self.assertIn(QUOTE, res.stdout)

    def test_import_reports_a_conflict_without_overwriting(self):
        self._cf("--workspace", str(self.alice.root), "share", "export", "--out", str(self.out))
        write_jsonl(self.bob.paths.accepted_jsonl("adam"), [ledger_row(verdict="UNSURE")])
        res = self._cf("--workspace", str(self.bob.root), "share", "import", str(self.out))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("conflict Exercise 1.1.1", res.stderr)
        self.assertIn("nothing was overwritten", res.stdout)

    def test_hydrate_refuses_to_write_into_the_export_set(self):
        self._cf("--workspace", str(self.alice.root), "share", "export", "--out", str(self.out))
        res = self._cf("--workspace", str(self.bob.root), "quotes", "hydrate",
                       "--bundle", str(self.out), "--out",
                       str(self.bob.root / "ledger" / "accepted.adam.jsonl"))
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("puts that path in a bundle", res.stderr)

    def test_hydrate_writes_the_recovered_text_where_it_is_gitignored(self):
        """And the rule really is there: the workspace is a repo carrying it.

        Asserting `"quotes/" in GITIGNORE` on its own was not enough -- the rule is
        only in workspaces created since it was written, which is why
        `_refuse_unsafe_out` asks git rather than trusting the constant.
        """
        from cfcmd.init import GITIGNORE

        subprocess.run(["git", "init", "-q", str(self.bob.root)], check=True,
                       capture_output=True)
        (self.bob.root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        self._cf("--workspace", str(self.alice.root), "share", "export", "--out", str(self.out))
        target = self.bob.root / "quotes" / "hydrated.adam.jsonl"
        res = self._cf("--workspace", str(self.bob.root), "quotes", "hydrate",
                       "--bundle", str(self.out), "--out", str(target), "--no-text")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        rows = read_jsonl(target)
        self.assertEqual(rows[0]["_meta"]["counts"]["hydrated"], 1)
        self.assertEqual(rows[1]["text"], QUOTE)
        self.assertNotIn(QUOTE, res.stdout)
        self.assertEqual(subprocess.run(
            ["git", "-C", str(self.bob.root), "check-ignore", "-q", str(target)],
            capture_output=True).returncode, 0)

    def test_hydrate_on_a_workspace_of_verbatim_rows_says_so(self):
        res = self._cf("--workspace", str(self.alice.root), "quotes", "hydrate", "--ledger")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("none with a redacted quote", res.stdout)
        self.assertIn("--bundle", res.stdout)

    def test_help_stays_accurate_for_both_commands(self):
        for args in (("share", "--help"), ("share", "export", "--help"),
                     ("share", "import", "--help"), ("quotes", "hydrate", "--help")):
            res = self._cf(*args)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("usage:", res.stdout)


# ==========================================================================
# security-safety#5 (code half) -- the reminder says it is a reminder
# ==========================================================================
class NoRemoteGuardTest(unittest.TestCase):
    def _cf(self, *args) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("CF_WORKSPACE", None)
        return subprocess.run([sys.executable, str(SCRIPTS / "cf.py"), *args],
                              capture_output=True, text=True, env=env, cwd=str(REPO))

    def _config(self, repo: Path, key: str) -> str:
        res = subprocess.run(["git", "-C", str(repo), "config", "--local", "--get", key],
                             capture_output=True, text=True)
        return res.stdout.strip()

    def test_cf_init_says_the_hook_is_a_reminder_that_no_verify_skips(self):
        """The finding: a second adopter reads the hook as enforcement."""
        root = scratch(self) / "ws"
        res = self._cf("init", str(root))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("--no-verify", res.stdout)
        self.assertIn("REMINDER", res.stdout)
        self.assertIn("NO REMOTE", res.stdout)
        self.assertIn("--no-remote-guard", res.stdout)

    def test_the_flag_sets_both_halves_of_the_guard(self):
        from cfcmd.init import NO_REMOTE_GUARD_REMOTE, no_remote_guard_set

        root = scratch(self) / "ws"
        res = self._cf("init", str(root), "--no-remote-guard")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._config(root, "remote.pushDefault"), NO_REMOTE_GUARD_REMOTE)
        self.assertEqual(self._config(root, "push.default"), "nothing")
        self.assertTrue(no_remote_guard_set(root))

    def test_a_bare_push_fails_under_the_guard_even_with_no_verify(self):
        """The guard's whole claim, measured: the hook is skipped, the push is not.

        The push is between two throwaway repositories in a temp directory; no
        remote of this machine is involved.
        """
        root = scratch(self) / "ws"
        self._cf("init", str(root), "--no-remote-guard")
        bare = scratch(self) / "bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                       capture_output=True)
        for key, value in (("user.email", "nobody@example.invalid"), ("user.name", "test")):
            subprocess.run(["git", "-C", str(root), "config", key, value], check=True,
                           capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "workspace.yaml"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "ws"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(bare)],
                       check=True, capture_output=True)
        pushed = subprocess.run(["git", "-C", str(root), "push", "--no-verify"],
                                capture_output=True, text=True)
        self.assertNotEqual(pushed.returncode, 0, pushed.stdout + pushed.stderr)
        self.assertEqual(subprocess.run(["git", "-C", str(bare), "log", "--oneline", "--all"],
                                        capture_output=True, text=True).stdout.strip(), "")

    def test_status_states_the_guard_when_it_is_set_and_does_not_nag_when_it_is_not(self):
        guarded = scratch(self) / "guarded"
        plain = scratch(self) / "plain"
        self._cf("init", str(guarded), "--no-remote-guard")
        self._cf("init", str(plain))
        res = self._cf("--workspace", str(guarded), "status")
        self.assertIn("bare-push guard set", res.stdout)
        res = self._cf("--workspace", str(plain), "status")
        self.assertNotIn("bare-push guard", res.stdout)
        self.assertIn("cf pre-push reminder installed", res.stdout)

    def test_status_json_carries_the_guard_as_a_fact(self):
        root = scratch(self) / "ws"
        self._cf("init", str(root), "--no-remote-guard")
        res = self._cf("--workspace", str(root), "status", "--json")
        self.assertTrue(json.loads(res.stdout)["workspace"]["git"]["no_remote_guard"])

    def test_the_hook_text_names_the_export_that_replaces_a_push(self):
        from cfcmd import init as init_cmd

        self.assertIn("--no-verify", init_cmd.PRE_PUSH_HOOK)
        self.assertIn("cf share export", init_cmd.PRE_PUSH_HOOK)
        self.assertIn("cf share export", init_cmd.GITIGNORE)
        # the prose an agent is pointed at must name the same two facts
        traps = (REPO / "references" / "traps.md").read_text(encoding="utf-8")
        self.assertIn("--no-remote-guard", traps)
        self.assertIn("cf share export", traps)
        # and so must the workspace.yaml `cf init` renders, where allow_remote
        # used to read as though the hook were the mechanism
        template = (REPO / "templates" / "workspace.yaml.example").read_text(encoding="utf-8")
        self.assertIn("REFUSES a workspace repo that has a remote", template)
        self.assertIn("--no-verify", template)

    def test_every_cf_command_the_new_messages_name_really_exists(self):
        """RULES: a `cf ...` command printed by the code must exist."""
        for args in (("share", "export", "--help"), ("quotes", "hydrate", "--help"),
                     ("init", "--help"), ("route", "--help"), ("adjudicate", "--help"),
                     ("sources", "refresh", "--help")):
            self.assertEqual(self._cf(*args).returncode, 0, args)
        self.assertIn("--no-remote-guard", self._cf("init", "--help").stdout)
        self.assertIn("--redact-quotes", self._cf("route", "--help").stdout)
        self.assertIn("--companion", self._cf("adjudicate", "--help").stdout)
        export_help = self._cf("share", "export", "--help").stdout
        self.assertIn("--skip-unchallenged", export_help)
        self.assertIn("policy.redact_quotes_on_export", export_help)
        self.assertIn("does not ignore",
                      self._cf("quotes", "hydrate", "--help").stdout)


# ==========================================================================
# the figures the hydration's docstring states, re-derived
# ==========================================================================
BASELINE_CORPUS = os.environ.get("CF_BASELINE_CORPUS", "")
BOOK_TXT = os.environ.get("CF_BOOK_TXT", "")
BASELINE_RUNS = ("findings", "findings-pdf", "findings-gpt", "findings-vac", "findings-hunt")


@unittest.skipUnless(BASELINE_CORPUS and BOOK_TXT,
                     "needs CF_BASELINE_CORPUS and CF_BOOK_TXT")
class RecordedHydrationFiguresTest(unittest.TestCase):
    """`share.hydrate`'s docstring quotes five numbers; this re-derives them.

    A figure nobody re-derives goes stale silently, and these are the figures the
    design rests on: a redacted quote is worth sharing only if a holder of the
    same edition really can get the sentence back.
    """

    @classmethod
    def setUpClass(cls):
        from gate.verify_findings import BookText, norm
        from ledger import findings as findings_mod

        cls.book = Path(BOOK_TXT).read_text(encoding="utf-8", errors="replace")
        text = BookText.from_text(cls.book, operators=False)
        rows = findings_mod.load_findings(
            [str(Path(BASELINE_CORPUS) / run) for run in BASELINE_RUNS])
        quotes = sorted({str(r.get("book_quote") or "") for r in rows
                         if str(r.get("book_quote") or "").strip()})
        cls.results = []
        for quote in quotes:
            record = redact_mod.redact_quote(quote, pages=text.pages_matching(norm(quote)))
            cls.results.append((quote, hydrate_mod.hydrate_record(record, cls.book)))

    def _counts(self) -> dict[str, int]:
        counts = {hydrate_mod.HYDRATED: 0, hydrate_mod.NOT_FOUND: 0, hydrate_mod.AMBIGUOUS: 0}
        for _, result in self.results:
            counts[result["status"]] += 1
        return counts

    def test_the_recorded_counts_are_the_measured_ones(self):
        self.assertEqual(len(self.results), 241)
        self.assertEqual(self._counts(), {hydrate_mod.HYDRATED: 214,
                                          hydrate_mod.NOT_FOUND: 25,
                                          hydrate_mod.AMBIGUOUS: 2})

    def test_only_a_quarter_come_back_byte_identical(self):
        """Why the digest is over the NORMALISED quote: the text wraps lines."""
        exact = sum(1 for quote, result in self.results
                    if result["status"] == hydrate_mod.HYDRATED and result["text"] == quote)
        self.assertEqual(exact, 25)

    def test_every_hydrated_passage_verifies_against_its_own_record(self):
        """The hash is the verification, so this must hold for all 214 of them."""
        for quote, result in self.results:
            if result["status"] != hydrate_mod.HYDRATED:
                continue
            record = redact_mod.redact_quote(quote)
            self.assertTrue(hydrate_mod.verify_text(record, result["text"]),
                            f"{quote[:60]!r} came back unverifiable")


# ==========================================================================
# critic#3, second pass -- the review's findings against the first one
# ==========================================================================
class ProseCarriesTheBookTooTest(unittest.TestCase):
    """`book_quote` was the only field redacted, so the book travelled in prose.

    MEASURED against the real ledger: 4 of refuted.adam.jsonl's 5 rows and 3 of
    accepted.adam.jsonl's 10 carried a run of 40 normalised characters or more that
    occurs verbatim in the book, the longest 113 in a `reason`, and one `reason`
    held a whole pdftotext page dump -- while the manifest said `quotes: redacted`
    and WITHHELD said the book text did not travel.
    """

    def setUp(self):
        self.workspace = make_workspace(self)
        self.reason = (f'the statement drops the finiteness half, though the book says '
                       f'"{LIFTED}" plainly')
        write_jsonl(self.workspace.paths.accepted_jsonl("adam"),
                    [ledger_row(reason=self.reason)])
        write_jsonl(self.workspace.paths.routed_jsonl("adam"),
                    [routed_row(reasoning=self.reason, concept=SHORT_LIFT)],
                    meta={"repo": "adam", "status": "CHALLENGED", "schema": "routed-v2"})

    def _rows(self, bundle: Path, name: str) -> list[dict]:
        path = (bundle / "ledger" / "routed" / name if name.startswith("findings.")
                else bundle / "ledger" / name)
        return [r for r in read_jsonl(path) if "_meta" not in r]

    def test_the_lifted_passage_is_long_enough_to_be_caught(self):
        """The fixture has to exercise the threshold, not sit either side of it."""
        self.assertGreaterEqual(len(redact_mod.normalise_quote(LIFTED)),
                                redact_mod.PROSE_SPAN_MIN)
        self.assertLess(len(redact_mod.normalise_quote(SHORT_LIFT)),
                        redact_mod.PROSE_SPAN_MIN)
        self.assertIn(LIFTED, SOURCE_TEXT)

    def test_no_prose_field_of_the_bundle_carries_a_run_of_the_source(self):
        """The load-bearing assertion, and the one the first pass could not make."""
        _, out = export(self, self.workspace)
        index = redact_mod.SourceIndex([SOURCE_TEXT])
        for name in ("accepted.adam.jsonl", "findings.adam.jsonl"):
            for row in self._rows(out, name):
                for field in redact_mod.PROSE_FIELDS:
                    value = row.get(field)
                    if not isinstance(value, str) or not value.strip():
                        continue
                    span = index.longest_span(redact_mod.normalise_quote(value))
                    self.assertIsNone(span, f"{name} {field}: {value[:80]!r}")
        self.assertNotIn(LIFTED, "\n".join(bundle_files(out).values()))

    def test_the_span_is_replaced_in_place_and_recorded_for_hydration(self):
        _, out = export(self, self.workspace)
        row = self._rows(out, "accepted.adam.jsonl")[0]
        self.assertIn("the statement drops the finiteness half", row["reason"])
        self.assertIn("[redacted:", row["reason"])
        spans = redact_mod.prose_spans(row)
        self.assertEqual([s["field"] for s in spans], ["reason"])
        self.assertEqual(spans[0]["quote"]["sha256"], redact_mod.quote_digest(LIFTED))
        self.assertEqual(spans[0]["quote"]["page"], QUOTE_PAGE)
        self.assertIn(spans[0]["quote"]["sha256"][:12], row["reason"])

    def test_a_run_below_the_threshold_is_left_alone(self):
        """A term of art is not a sentence of the book; the floor is measured."""
        _, out = export(self, self.workspace)
        row = self._rows(out, "findings.adam.jsonl")[0]
        self.assertEqual(row["concept"], SHORT_LIFT)

    def test_the_manifest_counts_the_substitutions_and_says_the_threshold(self):
        manifest, _ = export(self, self.workspace)
        self.assertEqual(manifest["counts"]["prose_spans"], 2)   # reason, twice over
        self.assertEqual(manifest["prose_span_min"], redact_mod.PROSE_SPAN_MIN)
        self.assertEqual(manifest["prose_checked_against"], [PUBLISHED])
        self.assertIn("verbatim run of the source inside a row's prose",
                      " ".join(manifest["withheld"]))

    def test_a_workspace_with_no_pinned_text_says_the_prose_was_not_checked(self):
        """The claim is unchecked without a text, and the export says so."""
        workspace = make_workspace(self, name="textless", text=None)
        with captured_warnings() as warned:
            manifest, _ = export(self, workspace)
        self.assertTrue(any("prose was NOT checked" in line for line in warned), warned)
        self.assertEqual(manifest["prose_checked_against"], [])

    def test_hydrate_recovers_a_prose_span_like_any_other_quote(self):
        _, out = export(self, self.workspace)
        rows = self._rows(out, "accepted.adam.jsonl")
        reports, counts = hydrate_mod.hydrate_rows(rows, {PUBLISHED: SOURCE_TEXT})
        spans = [r for r in reports if r["field"] == "reason"]
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["status"], hydrate_mod.HYDRATED)
        self.assertEqual(spans[0]["text"], LIFTED)
        self.assertEqual(counts[hydrate_mod.HYDRATED], 1)

    def test_a_page_dump_is_refused_by_name_rather_than_perforated(self):
        """Past the per-field limit the field is a page dump, and the row is named."""
        dump = " and ".join([PAGE_TWO, PAGE_THREE, PAGE_ONE] * 4)
        write_jsonl(self.workspace.paths.accepted_jsonl("adam"),
                    [ledger_row(reason=dump)])
        with self.assertRaises(CfError) as caught:
            export(self, self.workspace)
        self.assertIn("Exercise 1.1.1", str(caught.exception))
        self.assertIn("reason", str(caught.exception))
        self.assertIn("page dump", str(caught.exception))
        self.assertIn("--full-quotes", caught.exception.hint or "")

    def test_full_quotes_carries_the_prose_as_written(self):
        _, out = export(self, self.workspace, redact=False)
        self.assertIn(LIFTED, "\n".join(bundle_files(out).values()))

    def test_the_two_normalisations_are_one_function(self):
        """A page indexed one way and a span located the other would not hash alike."""
        raw = "  Let E\tbe   an\nELEMENTARY set. "
        norm, offsets = redact_mod.normalise_with_offsets(raw)
        self.assertEqual(norm, redact_mod.normalise_quote(raw))
        self.assertEqual(len(norm), len(offsets))
        self.assertEqual(raw[offsets[0]:offsets[len(norm) - 1] + 1].lower().split(),
                         norm.split())
        page = hydrate_mod._Page(1, raw)
        self.assertEqual((page.norm, page.offsets), (norm, offsets))


class NoLocalPathTravelsTest(unittest.TestCase):
    """A `_meta` row and a row's own prose both carried the exporter's machine.

    `cf adjudicate` writes `result_file: os.path.abspath(...)` and a
    `workflow_run_id` into the accepted/refuted `_meta`; `cf route` writes a
    `challenge_command` holding the absolute workspace path; and the baseline's own
    findings.adam.jsonl cites an absolute .lean path inside a `reasoning`.  RULES
    and CLAUDE.md S1 forbid all three in anything shareable.
    """

    def setUp(self):
        self.workspace = make_workspace(self)
        paths = self.workspace.paths
        write_jsonl(paths.accepted_jsonl("adam"), [ledger_row()], meta={
            "companion": "adam", "file": "accepted", "method": "workflow:adjudicate",
            "at": "2026-09-19", "counts": {"rows": 1},
            "result_file": str(self.workspace.root / "reviews" / "adjudicate.adam.json"),
            "workflow_run_id": "wf_ab12cd",
        })
        write_jsonl(paths.routed_jsonl("adam"), [routed_row(
            reasoning=f"see {self.workspace.root}/companions/adam/Basic.lean:324 for the proof",
        )], meta={
            "repo": "adam", "status": "CHALLENGED", "schema": "routed-v2",
            "counts": {"rows": 1}, "at": "2026-09-19",
            "challenge_command": f"cf adjudicate --workspace {self.workspace.root} --challenge",
        })

    def test_the_bundle_matches_none_of_the_greps_RULES_uses(self):
        _, out = export(self, self.workspace)
        blob = "\n".join(bundle_files(out).values())
        for forbidden in ("/Users/", "wf_", str(self.workspace.root), "adjudicate.adam.json"):
            self.assertNotIn(forbidden, blob, forbidden)

    def test_the_ledger_meta_keeps_what_a_reader_needs_and_drops_the_rest(self):
        _, out = export(self, self.workspace)
        meta = next(r["_meta"] for r in read_jsonl(out / "ledger" / "accepted.adam.jsonl")
                    if "_meta" in r)
        self.assertEqual(meta["companion"], "adam")
        self.assertEqual(meta["method"], "workflow:adjudicate")
        self.assertEqual(meta["counts"], {"rows": 1})
        for dropped in ("result_file", "workflow_run_id", "at"):
            self.assertNotIn(dropped, meta)

    def test_the_routed_meta_keeps_the_status_and_drops_the_command(self):
        _, out = export(self, self.workspace)
        meta = next(r["_meta"] for r in
                    read_jsonl(out / "ledger" / "routed" / "findings.adam.jsonl")
                    if "_meta" in r)
        self.assertEqual(meta["status"], "CHALLENGED")
        self.assertEqual(meta["schema"], "routed-v2")
        for dropped in ("challenge_command", "at"):
            self.assertNotIn(dropped, meta)

    def test_a_meta_that_is_all_withheld_travels_as_no_meta_at_all(self):
        """The baseline's refuted `_meta` is a session id, a date and a PDF path."""
        write_jsonl(self.workspace.paths.refuted_jsonl("adam"), [ledger_row(verdict="REFUTED")],
                    meta={"from": "session local_example", "written": "2026-09-04",
                          "official_pdf": {"path": "/somewhere/book.pdf"}})
        _, out = export(self, self.workspace)
        self.assertFalse([r for r in read_jsonl(out / "ledger" / "refuted.adam.jsonl")
                          if "_meta" in r])

    def test_an_absolute_path_in_a_rows_prose_keeps_its_file_and_line_only(self):
        _, out = export(self, self.workspace)
        row = [r for r in read_jsonl(out / "ledger" / "routed" / "findings.adam.jsonl")
               if "_meta" not in r][0]
        self.assertIn(".../Basic.lean:324", row["reasoning"])
        self.assertNotIn(str(self.workspace.root), row["reasoning"])

    def test_an_errata_url_is_metadata_and_is_left_alone(self):
        """The elision must not eat a URL: the registry's URLs are what a bundle IS."""
        text, count = redact_mod.elide_local_paths(
            "errata at https://example.invalid/books/analysis/errata.html")
        self.assertEqual((text, count),
                         ("errata at https://example.invalid/books/analysis/errata.html", 0))
        self.assertEqual(redact_mod.elide_local_paths("see /a/b/c.lean:12"),
                         ("see .../c.lean:12", 1))
        self.assertEqual(redact_mod.elide_local_paths("corpus/run-1/x.json"),
                         ("corpus/run-1/x.json", 0))


class UnchallengedRowsDoNotTravelTest(unittest.TestCase):
    """SKILL.md hard rule 5 binds the one command that writes outside the workspace.

    `route.emit_upstream` raises UpstreamRefused and `cfcmd/route.py` reads
    `policy.publish_unchallenged`; `export_bundle` read neither, and the real
    campaign's own routed file says `_meta.status: UNCHALLENGED`.
    """

    def setUp(self):
        self.workspace = make_workspace(self)

    def _unchallenged(self, status: str = "UNCHALLENGED") -> None:
        write_jsonl(self.workspace.paths.routed_jsonl("adam"), [routed_row(challenged=False)],
                    meta={"repo": "adam", "status": status, "schema": "routed-v2"})

    def test_an_unchallenged_routed_file_is_refused_by_name(self):
        self._unchallenged()
        with self.assertRaises(CfError) as caught:
            export(self, self.workspace)
        message = str(caught.exception)
        self.assertIn("adam", message)
        self.assertIn("UNCHALLENGED", message)
        self.assertIn("cf adjudicate", caught.exception.hint or "")
        self.assertIn("--skip-unchallenged", caught.exception.hint or "")

    def test_a_routed_file_with_no_status_at_all_is_refused_too(self):
        write_jsonl(self.workspace.paths.routed_jsonl("adam"), [routed_row()])
        with self.assertRaises(CfError) as caught:
            export(self, self.workspace)
        self.assertIn("(unset)", str(caught.exception))

    def test_skip_unchallenged_leaves_the_rows_out_and_names_them_in_the_manifest(self):
        self._unchallenged()
        manifest, out = export(self, self.workspace, skip_unchallenged=True)
        self.assertNotIn("ledger/routed/findings.adam.jsonl", bundle_files(out))
        self.assertEqual(manifest["skipped_unchallenged"], ["adam"])
        self.assertEqual(manifest["routed_status"]["adam"], "UNCHALLENGED")
        self.assertEqual(manifest["counts"]["routed_rows"], 0)
        # ...and the adjudicated half still travels, which is why this is not a flag
        # that carries unchallenged rows
        self.assertIn("ledger/accepted.adam.jsonl", bundle_files(out))
        self.assertIn("routed rows whose _meta.status is UNCHALLENGED",
                      " ".join(manifest["withheld"]))

    def test_a_challenged_routed_file_exports_unchanged_and_records_its_status(self):
        manifest, out = export(self, self.workspace)
        self.assertIn("ledger/routed/findings.adam.jsonl", bundle_files(out))
        self.assertEqual(manifest["routed_status"], {"adam": "CHALLENGED"})
        self.assertEqual(manifest["skipped_unchallenged"], [])
        self.assertEqual(manifest["counts"]["routed_rows"], 1)

    def test_a_workspace_that_turned_the_rule_off_is_refused_here_too(self):
        """`policy.publish_unchallenged: true` is read the way cfcmd/route.py reads it."""
        path = self.workspace.root / "workspace.yaml"
        path.write_text(path.read_text(encoding="utf-8")
                        + "policy:\n  publish_unchallenged: true\n", encoding="utf-8")
        workspace = Workspace.load(self.workspace.root)
        with self.assertRaises(CfError) as caught:
            export(self, workspace)
        self.assertIn("publish_unchallenged", str(caught.exception))
        self.assertIn("40%", caught.exception.hint or "")


class ImportConflictsAcrossBothLedgerFilesTest(unittest.TestCase):
    """The commonest disagreement is confirm-versus-refute, and it spans two files.

    accepted.* holds the OPEN verdicts and refuted.* the CLOSED ones, so merging
    one file at a time reported `added=1, conflicts=[]` for exactly the case the
    merge exists for, and both rows came out with `conflict: None`.
    """

    def setUp(self):
        self.alice = make_workspace(self, name="alice")        # CONFIRMED_CONTENT
        self.bob = make_workspace(self, name="bob", ledger=False)
        _, self.bundle = export(self, self.alice)
        self.manifest = bundle_mod.read_manifest(self.bundle)
        write_jsonl(self.bob.paths.refuted_jsonl("adam"),
                    [ledger_row(verdict="REFUTED", reason="the statement is faithful")])

    def _rows(self, which: str) -> list[dict]:
        path = getattr(self.bob.paths, f"{which}_jsonl")("adam")
        return [r for r in read_jsonl(path) if "_meta" not in r] if path.exists() else []

    def test_confirm_versus_refute_is_reported_and_both_rows_are_marked(self):
        report = merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertEqual(len(report["conflicts"]), 1)
        self.assertEqual(report["conflicts"][0]["verdicts"], ["CONFIRMED_CONTENT", "REFUTED"])
        self.assertEqual(report["conflicts"][0]["file"], "accepted.adam.jsonl")
        for which, verdict in (("accepted", "CONFIRMED_CONTENT"), ("refuted", "REFUTED")):
            rows = self._rows(which)
            self.assertEqual([r["verdict"] for r in rows], [verdict], which)
            self.assertTrue(rows[0]["conflict"], which)
            self.assertEqual(rows[0]["conflict_verdicts"],
                             ["CONFIRMED_CONTENT", "REFUTED"], which)

    def test_each_row_stays_in_the_file_its_verdict_belongs_to(self):
        """validate_ledger's partition rule still holds after a merge."""
        from ledger import compat as ledger_compat

        merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        for row in self._rows("accepted"):
            self.assertNotIn(row["verdict"], ledger_compat.CLOSED_VERDICTS)
        for row in self._rows("refuted"):
            self.assertIn(row["verdict"], ledger_compat.CLOSED_VERDICTS)

    def test_the_cross_file_conflict_import_is_idempotent(self):
        merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        before = {w: getattr(self.bob.paths, f"{w}_jsonl")("adam").read_bytes()
                  for w in ("accepted", "refuted")}
        report = merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertEqual((report["added"], report["unchanged"]), (0, 1))
        self.assertEqual(report["conflicts"], [])
        for which, blob in before.items():
            self.assertEqual(getattr(self.bob.paths, f"{which}_jsonl")("adam").read_bytes(),
                             blob, which)

    def test_the_marks_are_written_even_when_nothing_was_added_to_that_file(self):
        """The refuted row only gained marks, so counting additions would skip it."""
        merge_mod.merge_bundle(self.bob, self.bundle, self.manifest)
        self.assertTrue(self._rows("refuted")[0]["conflict"])

    def test_a_row_with_no_source_sha_of_its_own_is_refused_even_when_the_manifest_has_one(self):
        """CLAUDE.md S4 stamps the source per ROW; a manifest vouches for the bundle."""
        write_jsonl(self.bundle / "ledger" / "accepted.adam.jsonl", [ledger_row(source_sha="")])
        manifest = dict(self.manifest, sources={PUBLISHED: SHA_PUBLISHED})
        with self.assertRaises(CfError) as caught:
            merge_mod.merge_bundle(self.bob, self.bundle, manifest)
        self.assertIn("which edition", str(caught.exception))
        self.assertIn("a manifest vouches for the bundle", caught.exception.hint or "")


class HydrateOutRefusesATrackedPathTest(unittest.TestCase):
    """`quotes/` is ignored only in workspaces created after that line was written.

    `cf init` writes .gitignore through `_write_if_absent`, which leaves an
    existing file untouched, and `--force` would also rewrite workspace.yaml -- so
    in every workspace that already existed, the path this command's help suggests
    is a tracked file holding recovered book text.
    """

    def setUp(self):
        from cfcmd.init import GITIGNORE

        self.gitignore = GITIGNORE
        self.workspace = make_workspace(self, name="bob", ledger=False)
        subprocess.run(["git", "init", "-q", str(self.workspace.root)], check=True,
                       capture_output=True)

    def _refuse(self, target: Path) -> CfError:
        from cfcmd import quotes as quotes_cmd

        with self.assertRaises(CfError) as caught:
            quotes_cmd._refuse_unsafe_out(target.resolve(), self.workspace)
        return caught.exception

    def test_a_pre_change_gitignore_makes_the_advertised_path_a_refusal(self):
        before = self.gitignore.replace("quotes/\n", "")
        self.assertNotIn("\nquotes/", before)
        (self.workspace.root / ".gitignore").write_text(before, encoding="utf-8")
        error = self._refuse(self.workspace.root / "quotes" / "hydrated.adam.jsonl")
        self.assertIn("does not ignore that path", str(error))
        self.assertIn("sources", error.hint or "")

    def test_the_rule_cf_init_writes_today_makes_it_allowed(self):
        (self.workspace.root / ".gitignore").write_text(self.gitignore, encoding="utf-8")
        from cfcmd import quotes as quotes_cmd

        quotes_cmd._refuse_unsafe_out(
            (self.workspace.root / "quotes" / "hydrated.adam.jsonl").resolve(), self.workspace)
        quotes_cmd._refuse_unsafe_out(
            (self.workspace.root / "sources" / "hydrated.adam.jsonl").resolve(), self.workspace)

    def test_sources_is_the_hint_because_it_is_ignored_in_every_workspace(self):
        (self.workspace.root / ".gitignore").write_text(
            self.gitignore.replace("quotes/\n", ""), encoding="utf-8")
        from cfcmd.init import check_ignore

        self.assertTrue(check_ignore(self.workspace.root,
                                     self.workspace.root / "sources" / "x.jsonl"))
        self.assertFalse(check_ignore(self.workspace.root,
                                      self.workspace.root / "quotes" / "x.jsonl"))

    def test_a_workspace_with_no_repo_cannot_track_anything(self):
        """`cf init --no-git` is a documented choice, not a reason to refuse."""
        from cfcmd import quotes as quotes_cmd

        plain = make_workspace(self, name="nogit", ledger=False)
        quotes_cmd._refuse_unsafe_out(
            (plain.root / "quotes" / "hydrated.adam.jsonl").resolve(), plain)


class ExportReadsTheWorkspacePolicyTest(unittest.TestCase):
    """`policy.redact_quotes_on_export` was documented for both exports, read by one.

    The template's comment says the key governs `cf share export` too, and
    `cfcmd/route.py::_redact` exists precisely because a key an operator reads and
    the code ignores is worse than no key.
    """

    def setUp(self):
        self.workspace = make_workspace(self)

    def _policy(self, **over):
        path = self.workspace.root / "workspace.yaml"
        lines = ["policy:"] + [f"  {k}: {str(v).lower()}" for k, v in over.items()]
        path.write_text(path.read_text(encoding="utf-8") + "\n".join(lines) + "\n",
                        encoding="utf-8")
        return Workspace.load(self.workspace.root)

    def test_no_flag_means_the_policy_and_the_policy_defaults_to_redacting(self):
        from cfcmd import share as share_cmd

        class A:
            redact = None

        self.assertTrue(share_cmd._redact(self.workspace, A))
        workspace = self._policy(redact_quotes_on_export=False)
        self.assertFalse(share_cmd._redact(workspace, A))

    def test_a_flag_overrides_the_policy_in_both_directions(self):
        from cfcmd import share as share_cmd

        workspace = self._policy(redact_quotes_on_export=False)

        class Redact:
            redact = True

        class Full:
            redact = False

        self.assertTrue(share_cmd._redact(workspace, Redact))
        self.assertFalse(share_cmd._redact(workspace, Full))

    def test_the_template_comment_describes_what_the_code_now_does(self):
        template = (REPO / "templates" / "workspace.yaml.example").read_text(encoding="utf-8")
        self.assertIn("Read by BOTH exports", template)
        self.assertIn("share export --redact-quotes", template)
        self.assertIn("--full-quotes", template)


# ==========================================================================
# the real ledger, exported: the measurement the reviewer made, as a test
# ==========================================================================
BASELINE_DIFF = os.environ.get("CF_BASELINE_DIFF", "")


@unittest.skipUnless(BASELINE_DIFF and BOOK_TXT, "needs CF_BASELINE_DIFF and CF_BOOK_TXT")
class RealLedgerExportsCleanTest(unittest.TestCase):
    """Export the campaign's own accepted / refuted / routed rows and grep the result.

    This is the test the first pass could not fail: its fixtures only ever put book
    text in `book_quote`.  Here the rows are the real ones, and the assertions are
    the two the bundle's manifest claims -- no run of the source in a prose field,
    and no path or workflow id anywhere.
    """

    @classmethod
    def setUpClass(cls):
        cls.book = Path(BOOK_TXT).read_text(encoding="utf-8", errors="replace")
        cls.tmp = Path(tempfile.mkdtemp()).resolve()
        root = cls.tmp / "ws"
        repo = cls.tmp / "companion-repo"
        repo.mkdir(parents=True)
        root.mkdir(parents=True)
        (root / "workspace.yaml").write_text(
            workspace_yaml(name="baseline", repo=repo), encoding="utf-8")
        workspace = Workspace.load(root)
        paths = workspace.paths
        paths.ensure_skeleton()
        paths.companion_dir("adam").mkdir(parents=True, exist_ok=True)
        paths.source_dir(PUBLISHED).mkdir(parents=True, exist_ok=True)
        paths.source_text(PUBLISHED).write_text(cls.book, encoding="utf-8")
        for name, dest in (("accepted.adam.jsonl", paths.accepted_jsonl("adam")),
                           ("refuted.adam.jsonl", paths.refuted_jsonl("adam"))):
            shutil.copyfile(Path(BASELINE_DIFF) / name, dest)
        # the campaign's routed file is UNCHALLENGED, which is its own test above;
        # here the rows are what is under test, so they are staged as challenged
        rows = [r for r in read_jsonl(Path(BASELINE_DIFF) / "findings.adam.jsonl")
                if "_meta" not in r]
        write_jsonl(paths.routed_jsonl("adam"), rows,
                    meta={"repo": "adam", "status": "CHALLENGED", "schema": "routed-v2"})
        cls.workspace = workspace
        cls.out = cls.tmp / "bundle"
        cls.manifest = bundle_mod.export_bundle(workspace, cls.out)
        cls.index = redact_mod.SourceIndex([cls.book])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _exported_rows(self):
        for path in sorted(self.out.rglob("*.jsonl")):
            for row in read_jsonl(path):
                if "_meta" not in row:
                    yield path.name, row

    def test_the_rows_really_did_carry_the_book_in_their_prose(self):
        """The fixture is only worth anything if the unredacted rows fail this."""
        leaking = 0
        for name in ("accepted.adam.jsonl", "refuted.adam.jsonl", "findings.adam.jsonl"):
            for row in read_jsonl(Path(BASELINE_DIFF) / name):
                if "_meta" in row:
                    continue
                if any(self.index.longest_span(redact_mod.normalise_quote(row[f]))
                       for f in redact_mod.PROSE_FIELDS
                       if isinstance(row.get(f), str) and row[f].strip()):
                    leaking += 1
        self.assertEqual(leaking, 10)          # 3 accepted + 4 refuted + 3 routed

    def test_no_exported_prose_field_carries_a_run_of_the_book(self):
        """EVERY string of every row, not the seven keys the scrubber knows.

        Iterating `redact_mod.PROSE_FIELDS` here was the same enumeration the code
        made, so the test could not see a field the tuple omits.  The only strings
        exempt are the edge words a redaction record carries by design.
        """
        for name, row in self._exported_rows():
            for path, value in walk_strings(row):
                if path.startswith("book_quote") or path.startswith(redact_mod.SPANS_KEY):
                    continue
                span = self.index.longest_span(redact_mod.normalise_quote(value))
                self.assertIsNone(span, f"{name} {row.get('item')} {path}")
        self.assertEqual(self.manifest["counts"]["prose_spans"], 13)

    def test_the_bundle_matches_none_of_the_greps_RULES_uses(self):
        blob = "\n".join(bundle_files(self.out).values())
        for forbidden in ("/Users/", "/private/var/", str(self.workspace.root)):
            self.assertNotIn(forbidden, blob, forbidden)
        self.assertEqual(self.manifest["counts"]["paths_elided"], 1)

    def test_every_redaction_hydrates_back_out_of_the_holders_own_book(self):
        rows = [row for _, row in self._exported_rows()]
        reports, counts = hydrate_mod.hydrate_rows(hydrate_mod.redacted_rows(rows),
                                                  {PUBLISHED: self.book})
        self.assertEqual(counts["redacted"], 23)        # 10 book_quote + 13 prose spans
        self.assertEqual(counts[hydrate_mod.HYDRATED], 23)
        self.assertEqual([r for r in reports if r["status"] != hydrate_mod.HYDRATED], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
