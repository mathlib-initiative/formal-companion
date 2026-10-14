"""The annotation exporters: synthetic companions everywhere, the reference companions when named.

New code, testing scripts/annotations/export/ and `cf export`
(docs/proposals/pdf-to-companion.md section 5.4, milestone M3).  Two layers:

* **unit tests** over two synthetic companions built in a temporary git
  repository: a tiny attribution table (two geometry batches, a refinement, a
  ledger, destinations, one verdict) and two docstring-tagged Lean
  files with an injected PDF text layer, a cross-reference index and a
  two-page site.  Always run.
* **reproduction tests** against the pinned reference companions, skipped
  unless the environment names them:

      CF_MT        a measure_theory checkout at 2cd263a
      CF_MT_BUILD  its Lake build's lib/lean directory (default: <CF_MT>/.lake/build/lib/lean);
                   without the .ilean of the one module with anonymous instances, three
                   declarations lack their generated ``lean_name`` and the test says so
      CF_TML       a tao-measure-lean checkout at 677ada1
      CF_TML_SITE  its rendered literate site (default: <CF_TML>/_site when present)
      CF_TAO_PDF   the published edition's PDF (sha256 64498329...)

      CF_M3_REFERENCE_DIR  the maintainer-side reference documents, reports and
                   site facts (tests/data/m3/README.md lists them); never in this tree

  Each exporter must reproduce <CF_M3_REFERENCE_DIR>/<companion>.annotations.json.gz
  record for record.  The references are the exporters' own output, regenerated
  when a difference was deliberate; :data:`DELIBERATE` records what changed
  against the prototype documents the format was first written from, and the
  one environment-dependent field (the pdftotext version) is compared after
  normalisation.
"""
from __future__ import annotations

# The first import, before anything reads HOME (see tests/_hermetic.py).
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                               # noqa: E402
from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import ids                       # noqa: E402
from formal_companion_annotations import validate as V             # noqa: E402
from annotations.export import attribution_table, common, docstring_tags, site_pages  # noqa: E402
from annotations.export.pdftext import PdfText                     # noqa: E402
from cflib.errors import CfError                                   # noqa: E402

MT_COMMIT = "2cd263ac48d81b1270d67bf5ab7783018a35309a"
TML_COMMIT = "677ada107bd4fd49d7e10469c72f38699a251289"
PUBLISHED_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"

#: What the references changed against the prototype documents, deliberately.
DELIBERATE = (
    "companion.generator names `cf export <convention>` and the skill's version",
    "the PDF source is named through the registry: one id (tao-measure-gsm126-published), title and edition "
    "for both companions, where the prototypes wrote tao-measure-pdf and tao-gsm126-published; the ledger's "
    "own words for the edition stay on the measure_theory source as x_companion_edition",
    "the site source's title and retrieved_at come from the page manifest, not from the build's file times",
    "an equation's title (measure_theory: equation-1.2 and equation-1.3) carries title_source producer: a book "
    "prints an equation by its number alone, and the prototype's missing field read as the book's words",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.exists() else None


def reference_file(case: unittest.TestCase, name: str) -> Path:
    """``<CF_M3_REFERENCE_DIR>/<name>``, or skip ``case``: no private companion's data ships here."""
    ref_dir = env_path("CF_M3_REFERENCE_DIR")
    if ref_dir is None:
        case.skipTest("set CF_M3_REFERENCE_DIR to the M3 reference directory (tests/data/m3/README.md)")
    path = ref_dir / name
    if not path.is_file():
        case.skipTest(f"CF_M3_REFERENCE_DIR holds no {name} (tests/data/m3/README.md)")
    return path


def git_commit(root: Path) -> str:
    """Turn ``root`` into a one-commit git repository; return the commit id."""
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.invalid",
               GIT_AUTHOR_DATE="2026-09-01T12:00:00Z", GIT_COMMITTER_DATE="2026-09-01T12:00:00Z")
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True, env=env)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


def scratch(case: unittest.TestCase) -> Path:
    root = Path(tempfile.mkdtemp(prefix="cf-export-", dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
    case.addCleanup(shutil.rmtree, root, ignore_errors=True)
    return root


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def valid_and_quiet(case: unittest.TestCase, doc: dict) -> None:
    """The document validates canonically, passes the external-urls lint, and raises no warning."""
    text = V.canonical_dumps(V.canonicalize(doc))
    report = V.check(V.loads(text), text=text, canonical=True, lints=common.LINTS)
    case.assertEqual(report.problems, [])
    case.assertEqual(report.warnings, [])


def by_id(doc: dict, array: str) -> dict[str, dict]:
    return {r["id"]: r for r in doc.get(array, [])}


# ---------------------------------------------------------------------------
# a synthetic attribution-table companion
# ---------------------------------------------------------------------------

AT_LEAN = """\
namespace Demo

/-- Boxes. -/
structure Box where
  left : Nat
  right : Nat

/-- The volume of a box. -/
def Box.volume (b : Box) : Nat := b.right - b.left

/-- Finite additivity. -/
theorem volume_add (b : Box) : b.volume = b.volume := rfl

/-- A helper. -/
private theorem helper_bound (b : Box) : True := trivial

/- `example:demo-volume-nonneg` a worked example; the tag opens a longer comment,
so it declares no id. -/
example (b : Box) : b.volume = b.volume := rfl

/-- The declaration a fix introduced. -/
theorem replacement_stated (b : Box) : True := trivial

end Demo
"""

PDF_SHA = "11" * 32


def make_attribution_table_repo(root: Path) -> str:
    """Write the fixture companion under ``root``; return its commit."""
    (root / "Demo").mkdir(parents=True)
    (root / "Demo" / "Basic.lean").write_text(AT_LEAN, encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "source_formalization_ledger.py").write_text("# the ledger's generator\n")
    pdf = {"sha256": PDF_SHA, "physical_page_count": 20,
           "default_page_size_points": {"width": 612, "height": 792}}
    space = {"origin": "top_left", "units": "normalized", "x_axis": "right", "y_axis": "down"}

    def signoff(method="manual_visual"):
        return {"method": method, "verifier_id": "agent:verifier-1", "evidence_id": "ev-1",
                "evidence_sha256": "ab" * 32, "pdf_sha256": PDF_SHA}

    batch_a = {"schema": "x/geometry-batch", "schema_version": 1, "batch_id": "geometry-batch-a",
               "pdf": pdf, "coordinate_space": space, "items": [
                   {"item_id": "item:batch-a:def-1.1.1", "label": "Definition 1.1.1 (Boxes)",
                    "source_ids": ["src:1.1:boxes"],
                    "regions": [{"region_id": "region:batch-a:p007:def-1.1.1:01", "page": 7,
                                 "geometry": {"rect": [0.1, 0.2, 0.9, 0.3]}, "visual_signoff": signoff()}]},
                   {"item_id": "item:batch-a:thm-1.1.2", "label": "Theorem 1.1.2 and equation (1.1)",
                    "source_ids": ["src:1.1:additivity"],
                    "regions": [{"region_id": "region:batch-a:p008:thm-1.1.2:01", "page": 8,
                                 "geometry": {"rect": [0.1, 0.1, 0.9, 0.2]}, "visual_signoff": signoff()}]},
                   {"item_id": "item:batch-a:rem-volume", "label": "Unnumbered remark on volume",
                    "source_ids": ["src:1.1:volume"],
                    "regions": [{"region_id": "region:batch-a:p008:rem-volume:01", "page": 8,
                                 "geometry": {"quads": [[[0.1, 0.5], [0.9, 0.5], [0.9, 0.6], [0.1, 0.6]]]},
                                 "visual_signoff": signoff()}]},
                   {"item_id": "item:batch-a:ex-1.1.3", "label": "Exercise 1.1.3",
                    "source_ids": ["src:1.1:additivity"],
                    "regions": [{"region_id": "region:batch-a:p008:ex-1.1.3:01", "page": 8,
                                 "geometry": {"rect": [0.1, 0.7, 0.9, 0.8]}, "visual_signoff": signoff()},
                                {"region_id": "region:batch-a:p009:ex-1.1.3:01", "page": 9,
                                 "geometry": {"rect": [0.1, 0.1, 0.9, 0.2]}, "visual_signoff": signoff()}]},
               ]}
    batch_b = {"schema": "x/geometry-batch", "schema_version": 1, "batch_id": "geometry-batch-b",
               "pdf": pdf, "coordinate_space": space, "items": [
                   {"item_id": "item:batch-b:rem-1.1.4", "label": "Remark 1.1.4",
                    "source_ids": ["src:1.1:additivity"],
                    "regions": [{"region_id": "region:batch-b:p009:rem-1.1.4:01", "page": 9,
                                 "geometry": {"rect": [0.1, 0.3, 0.9, 0.4]},
                                 "visual_signoff": signoff("manual_visual")}]}]}
    data = root / "pdf-overlay" / "data"
    write_json(data / "geometry-batch-a.json", batch_a)
    write_json(data / "geometry-batch-b.json", batch_b)
    write_json(data / "geometry-batch-b.sources.json", {"src:1.1:additivity": ["Remark 1.1.4"]})
    write_json(root / "pdf-overlay" / "official-source.json", {
        "schema": "x/official-pdf-source", "schema_version": 1, "sha256": PDF_SHA, "bytes": 1234,
        "canonical_pdf_url": "https://example.org/demo.pdf", "canonical_page_url": "https://example.org/demo",
        "final_pdf_url": "https://example.org/demo-final.pdf", "legacy_page_url": "https://example.org/old",
        "retrieved_at_utc": "2026-09-01T00:00:00Z", "pdf_header": "%PDF-1.4", "pdf_eof": "%%EOF",
        "redirect_chain": []})
    nodes = [
        {"id": "Demo.Box", "kind": "structure"}, {"id": "Demo.Box.left", "kind": "structure field"},
        {"id": "Demo.Box.volume", "kind": "definition"}, {"id": "Demo.volume_add", "kind": "theorem"},
        {"id": "private:Demo.Basic:Demo/Basic.lean:L99:helper_bound", "kind": None},
        {"id": "example:demo-volume-nonneg", "kind": "anonymous example"},
        {"id": "example:demo-unlocated", "kind": "anonymous example"},
        {"id": "moddoc:Demo.Basic", "kind": "module document"},
    ]
    for n in nodes:
        n.update(module="Demo.Basic", file="Demo/Basic.lean")

    def edge(fid, role, src, realization="implemented"):
        e = {"formalization_id": fid, "role": role, "source_id": src}
        if realization:
            e["realization"] = realization
        return e

    ledger = {
        "ledger_schema": "x/source-formalization-ledger", "schema_version": 1, "scope": "demo-chapter-1",
        "source": {"kind": "pdf", "path": "sources/demo.pdf", "revision": "the demo revision", "sha256": PDF_SHA},
        "formalization_snapshot": {"commit": "e" * 40, "project": "demo", "tree": "f" * 40},
        "source_nodes": [
            {"id": "src:1.1:boxes", "locator": "Section 1.1, Definition 1.1.1, printed pp. 5--6, PDF pp. 7--8",
             "plan_item_ids": ["plan:p1"], "status": "implemented", "semantic_status": "verified",
             "external_admissions": []},
            {"id": "src:1.1:additivity", "locator": "Section 1.1, Theorem 1.1.2, printed p. 6, PDF p. 8",
             "plan_item_ids": ["plan:p2"], "status": "implemented", "semantic_status": "verified",
             "external_admissions": []},
            {"id": "src:1.1:volume", "locator": "Section 1.1, unnumbered, printed p. 6, PDF p. 8",
             "plan_item_ids": [], "status": "implemented", "semantic_status": "verified",
             "external_admissions": []},
        ],
        "formalization_nodes": nodes,
        "edges": [
            edge("Demo.Box", "primary", "src:1.1:boxes"), edge("Demo.Box.left", "primary", "src:1.1:boxes"),
            edge("Demo.Box.volume", "primary", "src:1.1:boxes"), edge("moddoc:Demo.Basic", "support", "src:1.1:boxes"),
            edge("Demo.volume_add", "primary", "src:1.1:additivity"),
            edge("example:demo-unlocated", "primary", "src:1.1:additivity"),
            edge("private:Demo.Basic:Demo/Basic.lean:L99:helper_bound", "consumer", "src:1.1:additivity", None),
            edge("Demo.Box.volume", "primary", "src:1.1:volume"),
            edge("example:demo-volume-nonneg", "support", "src:1.1:volume"),
        ],
        "files": [], "counts": {}, "comparison_signatures": {}, "authority": {}, "provenance": {}}
    write_json(root / "source-formalization-ledger.json", ledger)
    ledger_bytes = (root / "source-formalization-ledger.json").read_bytes()
    ledger_sha = sha256(ledger_bytes)
    write_json(data / "target-refinement.json", {
        "schema": "x/target-refinement", "schema_version": 2, "method": "agent_semantic_curation",
        "ledger_sha256": ledger_sha,
        "provenance": {"confirmed_by_human": False, "nodes_curated": 2,
                       "note": "Drafted by one curator agent. Batch b (section 1.1, 5 items in 3 source nodes) "
                               "was curated the same way; the coverage entries say which items no declaration "
                               "states in full."},
        "entries": [
            {"region_id": "region:batch-a:p007:def-1.1.1:01", "item_id": "item:batch-a:def-1.1.1",
             "formalization_ids": ["Demo.Box", "Demo.Box.left", "Demo.Box.volume"],
             "key_formalization_ids": ["Demo.Box", "Demo.Box.left"]},
            {"region_id": "region:batch-a:p008:thm-1.1.2:01", "item_id": "item:batch-a:thm-1.1.2",
             "formalization_ids": ["Demo.volume_add"], "key_formalization_ids": ["Demo.volume_add"]},
            {"region_id": "region:batch-a:p008:rem-volume:01", "item_id": "item:batch-a:rem-volume",
             "formalization_ids": ["Demo.Box.volume"], "key_formalization_ids": ["Demo.Box.volume"]},
            # coverage entries: a class, the declarations it admits, no key, the curator's note
            {"region_id": "region:batch-a:p008:ex-1.1.3:01", "item_id": "item:batch-a:ex-1.1.3",
             "coverage": "support_only", "formalization_ids": ["Demo.volume_add"], "key_formalization_ids": [],
             "note": "support only: volume_add states Theorem 1.1.2, which the exercise asks the reader to use"},
            {"region_id": "region:batch-a:p009:ex-1.1.3:01", "item_id": "item:batch-a:ex-1.1.3",
             "coverage": "support_only", "formalization_ids": ["Demo.volume_add"], "key_formalization_ids": [],
             "note": "support only: volume_add states Theorem 1.1.2, which the exercise asks the reader to use"},
            {"region_id": "region:batch-b:p009:rem-1.1.4:01", "item_id": "item:batch-b:rem-1.1.4",
             "coverage": "partial_only", "formalization_ids": ["Demo.Box.volume"], "key_formalization_ids": [],
             "note": "partial only: Box.volume states the first half of the remark; the bound is not stated"},
        ]})
    write_json(data / "destinations.json", {
        "schema": "x/pdf-overlay-destinations", "schema_version": 1,
        "ledger": {"path": "source-formalization-ledger.json", "sha256": ledger_sha, "bytes": len(ledger_bytes)},
        "pdf": pdf,
        "support_relation": {"source_id": "src:1.1:volume", "related_to": "src:1.1:boxes", "role": "support",
                             "relation_id": "rel-1"},
        "destinations": [
            {"formalization_id": "Demo.Box", "resolution": "exact", "exact_href": "Demo/Basic/#Demo___Box"},
            {"formalization_id": "moddoc:Demo.Basic", "resolution": "fallback", "fallback_href": "Demo/Basic/",
             "fallback_label": "module page"},
            {"formalization_id": "example:demo-volume-nonneg", "resolution": "fallback",
             "fallback_href": "Demo/Basic/", "fallback_label": "module page"}]})
    commit = git_commit(root)
    write_json(data / "faithfulness-verdicts.json", {
        "schema": "x/faithfulness-verdicts", "schema_version": 1, "ledger_sha256": ledger_sha,
        "method": "a three-way differential adjudicated by two skeptics, 2026-09-05",
        "entries": [{
            "region_id": "region:batch-a:p008:thm-1.1.2:01", "verdict": "attribution_gap", "severity": "low",
            "status": "fixed", "summary": "Fixed in PR #3. Theorem 1.1.2(ii) was stated by a helper.",
            "evidence": [f"The fix landed in commit {commit[:9]} (ATTRIBUTION_GAP, low)."],
            "declarations": [{"id": "Demo.volume_add", "verdict": "faithful",
                              "note": "Fixed in PR #3: replacement_stated (Basic.lean:20) states it."}]}]})
    # the verdicts file names the commit, so it is committed after it exists
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.invalid")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "verdicts"], check=True, env=env)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


class TestAttributionTableExport(unittest.TestCase):
    """The synthetic overlay companion becomes a valid document with every record shape."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="cf-export-at-",
                                         dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
        cls.commit = make_attribution_table_repo(cls.root)
        cls.doc, cls.report = attribution_table.export(
            cls.root, cls.commit[:10], name="demo", repo_id="git.example.org/demo/demo",
            source_id="demo-pdf", source_title="A Demo Book", source_edition="first")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_it_validates_canonically_without_a_warning(self):
        valid_and_quiet(self, self.doc)
        self.assertEqual(self.report.problems, [])

    def test_the_profiles_are_exactly_the_ones_the_document_uses(self):
        # clusters and companion.graph (graph), snippets and destinations (site), verdicts and source.role
        # (audit), the ordinal changelog (history)
        self.assertEqual(self.doc["profiles"], {"audit": "1.0", "graph": "1.0", "history": "1.0", "site": "1.0"})
        self.assertEqual(self.report.counts["profiles"], self.doc["profiles"])

    def test_the_source_takes_the_offset_from_the_ledger_prose_and_the_edition_from_the_flags(self):
        src = self.doc["sources"][0]
        self.assertEqual(src["id"], "demo-pdf")
        self.assertEqual(src["printed_page_offset"], 2)
        self.assertEqual(src["edition"], "first")
        self.assertEqual(src["x_companion_edition"], "the demo revision")
        self.assertEqual(src["alternate_urls"], ["https://example.org/demo", "https://example.org/demo-final.pdf",
                                                 "https://example.org/old"])

    def test_items_numbered_split_and_passage(self):
        items = by_id(self.doc, "items")
        self.assertEqual(items["definition-1.1.1"]["title"], "Boxes")
        self.assertEqual(items["definition-1.1.1"]["legacy_ids"], ["item:batch-a:def-1.1.1"])
        # one label naming two book items gives two items sharing the location
        self.assertIn("theorem-1.1.2", items)
        self.assertEqual(items["equation-1.1"]["kind"], "Equation")
        self.assertNotIn("legacy_ids", items["equation-1.1"])
        loc = by_id(self.doc, "locations")["theorem-1.1.2@p008"]
        self.assertEqual(loc["items"], ["equation-1.1", "theorem-1.1.2"])
        self.assertEqual(loc["x_item_order"], ["theorem-1.1.2", "equation-1.1"], "the order the label names them in")
        self.assertNotIn("x_item_order", by_id(self.doc, "locations")["exercise-1.1.3@p009"])
        # the unnumbered region is a passage titled by the producer, keyed by printed page and title
        pid = ids.passage_page_id(6, "Remark on volume")
        self.assertEqual(items[pid]["title_source"], "producer")
        self.assertEqual(items[pid]["pages"], {"first": 8, "basis": "physical", "anchor": "own"})
        # two regions on different pages of one item are two locations
        self.assertIn("exercise-1.1.3@p009", by_id(self.doc, "locations"))

    def test_regions_become_pdf_region_selectors_with_a_highlight_fallback(self):
        loc = by_id(self.doc, "locations")["definition-1.1.1@p007"]
        first, second = loc["target"]["selector"]
        self.assertEqual(first["refinedBy"]["rect"], {"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.3})
        self.assertEqual(second["value"], "page=7&highlight=61,551,158,238")
        self.assertEqual(loc["signoff"]["verifier"], "agent:verifier-1")
        quads = by_id(self.doc, "locations")[ids.passage_page_id(6, "Remark on volume") + "@p008"]
        self.assertIn("quads", quads["target"]["selector"][0]["refinedBy"])
        self.assertEqual(self.report.lossy["location.quads_highlight"]["count"], 1)

    def test_declarations_from_the_ledger_and_the_lean(self):
        decls = by_id(self.doc, "declarations")
        self.assertEqual(decls["Demo.Box"]["kind"], "structure")
        self.assertNotIn("signature", decls["Demo.Box"])
        self.assertEqual(decls["Demo.Box.left"]["kind"], "field")
        self.assertEqual(decls["Demo.Box.left"]["parent"], "Demo.Box")
        self.assertEqual(decls["Demo.Box.left"]["namespace"], "Demo.Box")
        self.assertEqual(decls["Demo.volume_add"]["signature"],
                         "theorem volume_add (b : Box) : b.volume = b.volume")
        helper = decls["private:Demo.Basic:Demo.helper_bound"]
        self.assertEqual(helper["legacy_ids"], ["private:Demo.Basic:Demo/Basic.lean:L99:helper_bound"])
        self.assertEqual(self.report.lossy["declaration.private_line_stale"]["count"], 1)
        # the comment tag declares no id (it opens a longer comment) but joins the ledger through legacy_ids
        hashed = [d for d in decls.values() if d["anonymous"] and not d.get("unlocated")]
        self.assertEqual(len(hashed), 1)
        self.assertEqual(hashed[0]["legacy_ids"], ["example:demo-volume-nonneg"])
        self.assertEqual(hashed[0]["id_source"], "statement_hash")
        self.assertTrue(decls["example:demo-unlocated"]["unlocated"])
        self.assertEqual(decls["Demo.replacement_stated"]["kind"], "theorem")
        self.assertEqual(self.report.counts["declarations_added_as_replacements"], ["Demo.replacement_stated"])

    def test_links_memberships_and_the_support_edge_that_becomes_an_example_link(self):
        links = by_id(self.doc, "links")
        self.assertEqual(links["definition-1.1.1/key/Demo.Box.left"]["role"], "key")
        self.assertEqual(links["definition-1.1.1/attributed/Demo.Box.volume"]["role"], "attributed")
        hashed = next(d for d in self.doc["declarations"] if d["anonymous"] and not d.get("unlocated"))
        pid = ids.passage_page_id(6, "Remark on volume")
        self.assertEqual(links[f"{pid}/example/{hashed['id']}"]["provenance"], "prov-ledger")
        members = by_id(self.doc, "memberships")
        self.assertIn("src:1.1:boxes/support/moddoc:Demo.Basic", members)
        self.assertEqual(members["src:1.1:boxes/support/moddoc:Demo.Basic"]["artifact"], "moddoc:Demo.Basic")
        clusters = by_id(self.doc, "clusters")
        self.assertEqual(clusters["src:1.1:volume"]["related_to"], ["src:1.1:boxes"])
        self.assertEqual(clusters["src:1.1:additivity"]["items"],
                         ["equation-1.1", "exercise-1.1.3", "remark-1.1.4", "theorem-1.1.2"])

    def test_coverage_entries_give_the_class_the_note_and_the_links_the_class_admits(self):
        items, links = by_id(self.doc, "items"), by_id(self.doc, "links")
        # support_only: the listed declaration is a support link; the two regions' entries merge into one
        self.assertEqual(items["exercise-1.1.3"]["coverage"], "support_only")
        self.assertTrue(items["exercise-1.1.3"]["coverage_note"].startswith("support only:"))
        support = links["exercise-1.1.3/support/Demo.volume_add"]
        self.assertEqual((support["role"], support["provenance"]), ("support", "prov-target-refinement"))
        self.assertNotIn("relation", support)
        # partial_only: the listed declaration is an attributed link with relation part (the note says no special case)
        self.assertEqual(items["remark-1.1.4"]["coverage"], "partial_only")
        partial = links["remark-1.1.4/attributed/Demo.Box.volume"]
        self.assertEqual((partial["role"], partial["relation"]), ("attributed", "part"))
        self.assertEqual(self.report.bent["item.coverage_from_table"]["count"], 2)
        self.assertEqual(self.report.counts["coverage_entries"],
                         {"partial_only": 1, "partial_only/attributed": 1, "support_only": 1,
                          "support_only/support": 1})

    def test_an_item_with_clusters_and_no_links_is_the_fallback_and_carries_no_flag(self):
        items = by_id(self.doc, "items")
        # the equation split off Theorem 1.1.2's label has no entry: shown through its clusters, no coverage
        self.assertEqual(items["equation-1.1"]["clusters"], ["src:1.1:additivity"])
        self.assertNotIn("cluster_fallback", items["equation-1.1"])
        self.assertFalse(any("cluster_fallback" in i for i in self.doc["items"]))
        self.assertNotIn("coverage", items["equation-1.1"])
        self.assertEqual(self.report.counts["items_with_clusters_and_no_links"], 1)
        self.assertEqual(self.report.lossy["item.coverage_unclassified"]["examples"], ["equation-1.1"])

    def test_the_verdict_carries_its_resolution_from_prose_and_git(self):
        verdict = by_id(self.doc, "verdicts")["verdict:theorem-1.1.2"]
        self.assertEqual(verdict["sub"], "ii")
        self.assertNotIn("ledger_verdict", verdict)
        self.assertEqual(verdict["x_ledger_verdict"], "ATTRIBUTION_GAP")
        self.assertEqual(self.report.bent["verdict.x_ledger_verdict"]["count"], 1)
        res = verdict["resolution"]
        self.assertEqual(res["state"], "fixed")
        self.assertEqual(res["pr"], 3)
        self.assertEqual(len(res["commits"]), 1)
        self.assertEqual(len(res["commits"][0]), 40)
        self.assertEqual(res["replaced_by"], ["Demo.replacement_stated"])
        self.assertEqual(by_id(self.doc, "provenance")["prov-verdicts-differential"]["at"], "2026-09-05")

    def test_provenance_names_the_batches_the_ledger_and_the_tools_that_exist(self):
        prov = by_id(self.doc, "provenance")
        self.assertEqual(prov["prov-geometry-a"]["method"], "manual")
        self.assertIn("commit", prov["prov-geometry-a"]["note"])
        self.assertEqual(prov["prov-geometry-b"]["method"], "layout_extraction")
        self.assertNotIn("tool", prov["prov-geometry-b"])          # the fixture ships no generator
        self.assertEqual(prov["prov-ledger"]["tool"]["path"], "scripts/source_formalization_ledger.py")
        self.assertEqual(self.report.lossy["provenance.geometry_tool_missing"]["count"], 1)

    def test_snippets_destinations_and_the_graph_pin(self):
        # one snippet per declaration the refinement lists, cut from the Lean at the commit
        snippets = by_id(self.doc, "snippets")
        self.assertEqual(sorted(snippets), ["snippet:Demo.Box", "snippet:Demo.Box.left", "snippet:Demo.Box.volume",
                                            "snippet:Demo.volume_add"])
        self.assertEqual((snippets["snippet:Demo.volume_add"]["line"], snippets["snippet:Demo.volume_add"]["text"]),
                         (12, "theorem volume_add (b : Box) : b.volume = b.volume := rfl"))
        self.assertEqual(snippets["snippet:Demo.Box.volume"]["text"],
                         "def Box.volume (b : Box) : Nat := b.right - b.left")
        # a field has no declaration line of its own at HEAD: it takes its snippet's
        self.assertEqual(snippets["snippet:Demo.Box.left"]["text"], "structure Box where\n  …\n  left : Nat")
        self.assertEqual(by_id(self.doc, "declarations")["Demo.Box.left"]["line"], 5)
        self.assertFalse(any(s.get("truncated") for s in snippets.values()))
        self.assertEqual(self.report.counts["snippets_unlocated"], [])
        self.assertNotIn("ignored_inputs", self.report.counts)
        self.assertNotIn("pdf-overlay/data/declaration-snippets.json",
                         [i["path"] for i in self.report.counts["inputs"]])
        dests = by_id(self.doc, "destinations")
        self.assertEqual(dests["verso_literate:Demo.Box"]["fragment"], "Demo___Box")
        self.assertEqual(dests["verso_literate:moddoc:Demo.Basic"]["artifact"], "moddoc:Demo.Basic")
        self.assertEqual(self.doc["companion"]["graph"]["snapshot_commit"], "e" * 40)
        self.assertEqual(self.doc["x_graph_scope"]["scope"], "demo-chapter-1")

    def test_a_checkout_at_another_commit_or_dirty_is_refused(self):
        with self.assertRaises(common.ExportError):
            attribution_table.export(self.root, "0" * 12, name="demo", repo_id="x/y/z", source_id="demo-pdf",
                                     source_title="t")
        (self.root / "Demo" / "Basic.lean").write_text(AT_LEAN + "\n-- dirty\n", encoding="utf-8")
        try:
            with self.assertRaises(common.ExportError) as caught:
                attribution_table.export(self.root, self.commit, name="demo", repo_id="x/y/z",
                                         source_id="demo-pdf", source_title="t")
            self.assertIn("uncommitted", caught.exception.message)
        finally:
            (self.root / "Demo" / "Basic.lean").write_text(AT_LEAN, encoding="utf-8")

    def test_the_command_writes_the_document_and_the_report(self):
        out = scratch(self) / "demo.annotations.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cf_main.main(["export", "attribution-table", str(self.root), "--commit", self.commit,
                                   "--out", str(out), "--name", "demo", "--repo-id", "git.example.org/demo/demo",
                                   "--source-id", "demo-pdf", "--source-title", "A Demo Book"])
        self.assertEqual(status, 0, stderr.getvalue())
        text = out.read_text(encoding="utf-8")
        self.assertEqual(text, V.canonical_dumps(V.canonicalize(V.loads(text))))
        report = json.loads(out.with_name("demo.export-report.json").read_text(encoding="utf-8"))
        self.assertEqual(set(report), {"counts", "lossy", "bent", "problems"})
        self.assertIn("wrote", stdout.getvalue())

    def test_the_command_refuses_without_a_convention(self):
        with self.assertRaises(CfError):
            cf_main.main(["export"])


class TestCoverageEntries(unittest.TestCase):
    """The table's coverage entries (schema version 2), each class on a variant of the synthetic companion."""

    @staticmethod
    def set_entry(root: Path, item_id: str, **fields) -> None:
        def change(table):
            for e in table["entries"]:
                if e["item_id"] == item_id:
                    e.update(fields)
        edit_json(root / "pdf-overlay" / "data" / "target-refinement.json", change)

    def test_a_note_that_says_special_case_sets_the_relation(self):
        root, commit = at_variant(self, lambda r: self.set_entry(
            r, "item:batch-b:rem-1.1.4", note="partial only: Box.volume states the special case of one box"))
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        self.assertEqual(by_id(doc, "links")["remark-1.1.4/attributed/Demo.Box.volume"]["relation"], "special_case")
        self.assertEqual(report.bent["link.relation_from_note"]["examples"], ["remark-1.1.4"])

    def test_not_formalized_lists_nothing_and_the_item_falls_back_to_its_clusters(self):
        root, commit = at_variant(self, lambda r: self.set_entry(
            r, "item:batch-a:ex-1.1.3", coverage="not_formalized", formalization_ids=[], note="nothing"))
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        item = by_id(doc, "items")["exercise-1.1.3"]
        self.assertEqual((item["coverage"], item["coverage_note"]), ("not_formalized", "nothing"))
        self.assertFalse(any(ln["item"] == "exercise-1.1.3" for ln in doc["links"]))
        self.assertEqual(report.counts["items_with_clusters_and_no_links"], 2)
        # classified, so not counted as unclassified
        self.assertEqual(report.lossy["item.coverage_unclassified"]["examples"], ["equation-1.1"])

    def test_example_only_and_private_only_take_the_roles_i5_admits(self):
        def mutate(root):
            self.set_entry(root, "item:batch-a:ex-1.1.3", coverage="example_only",
                           formalization_ids=["example:demo-volume-nonneg", "Demo.volume_add"],
                           note="example only: the worked example states it")
            self.set_entry(root, "item:batch-b:rem-1.1.4", coverage="private_only",
                           formalization_ids=["private:Demo.Basic:Demo/Basic.lean:L99:helper_bound"],
                           note="private only: helper_bound states it")
        root, commit = at_variant(self, mutate)
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        links = by_id(doc, "links")
        hashed = next(d["id"] for d in doc["declarations"] if d["anonymous"] and not d.get("unlocated"))
        self.assertEqual(links[f"exercise-1.1.3/example/{hashed}"]["role"], "example")
        # a named theorem under example_only is not what the class names: a support link, reported
        self.assertEqual(links["exercise-1.1.3/support/Demo.volume_add"]["role"], "support")
        self.assertEqual(report.bent["link.support_role_from_coverage_class"]["count"], 1)
        self.assertEqual(links["remark-1.1.4/key/private:Demo.Basic:Demo.helper_bound"]["role"], "key")
        self.assertEqual(report.counts["coverage_entries"]["private_only/key"], 1)

    def test_a_coverage_entry_with_key_ids_or_disagreeing_regions_is_reported(self):
        root, commit = at_variant(self, lambda r: self.set_entry(
            r, "item:batch-b:rem-1.1.4", key_formalization_ids=["Demo.Box.volume"]))
        _doc, report = at_export(root, commit)
        self.assertTrue(any("names key ids" in p for p in report.problems))

        def disagree(root):
            def change(table):
                table["entries"][4]["coverage"] = "not_formalized"
            edit_json(root / "pdf-overlay" / "data" / "target-refinement.json", change)
        root, commit = at_variant(self, disagree)
        doc, report = at_export(root, commit)
        self.assertTrue(any("disagree on coverage" in p for p in report.problems))
        self.assertNotIn("coverage", by_id(doc, "items")["exercise-1.1.3"])

    def test_a_version_1_table_is_refused(self):
        root, commit = at_variant(self, lambda r: edit_json(
            r / "pdf-overlay" / "data" / "target-refinement.json", lambda o: o.update(schema_version=1)))
        with self.assertRaises(common.ExportError) as caught:
            at_export(root, commit)
        self.assertIn("schema_version 1", caught.exception.message)
        self.assertIn("version 2", caught.exception.message)

    def test_a_coverage_note_with_a_url_is_dropped_and_reported(self):
        root, commit = at_variant(self, lambda r: self.set_entry(
            r, "item:batch-a:ex-1.1.3", note="support only, see https://example.org/why"))
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        self.assertNotIn("coverage_note", by_id(doc, "items")["exercise-1.1.3"])
        self.assertTrue(any("external-urls" in p for p in report.problems))


# ---------------------------------------------------------------------------
# a synthetic docstring-tags companion
# ---------------------------------------------------------------------------

DT_LEAN_1 = """\
namespace Demo

/-- `Definition 1.1.1` (Boxes).  A *box* is a product of intervals. -/
def IsBox (s : Nat) : Prop := True

/-- `Exercise 1.1.3(ii)` (book p. 12; the second part only).  Left to the reader. -/
theorem ex_113 : True := by
  sorry

/-- `In-text, book p. 12` ("every box is elementary" as the book says). -/
theorem box_elementary : True := trivial

/-- `In-text proof of Lemma 1.1.2(i), book p. 6` (the sorting argument). -/
theorem helper_step : True := trivial

/-- `In-text definition, Exercise 1.1.3` (a passage placed by its neighbour). -/
def Placed : Prop := True

/-- Infrastructure (API) with no tag. -/
theorem api_thing : True := trivial

/-- `Example 1.1.9`.  An anonymous worked example. -/
example : (1 : Nat) = 1 := rfl

private theorem hidden_helper : True := trivial

end Demo
"""

DT_LEAN_2 = """\
namespace Demo

/-- `Lemma 1.2.5` (Finite additivity).  Disjointness alone is not enough. -/
theorem lemma_125 : True := trivial

end Demo
"""

DT_ADAPTER = """\
name: demo
repo: <set by the exporter>
adapter: docstring_tags
granularity: item
sources:
  default: demo-pdf
  map: []
glob: "Demo/*.lean"
item_kinds: [Definition, Lemma, Theorem, Proposition, Corollary, Exercise, Example, Remark, Strategy, Claim]
tag:
  position: docstring_start
  inline_window: 0
  require_backticks: true
  pattern: null
  page_pattern: '(?:book\\s+)?(?:pp?\\.|pages?)\\s*(\\d+)(?:\\s*[-]\\s*(\\d+))?'
  clause_window: 140
anchor: docstring
decl_kinds: [theorem, lemma, def, abbrev, structure, class, inductive, instance, example, opaque]
body_for_kinds: [def, abbrev, instance, structure, class, inductive]
max_body_lines: 40
sorry:
  exercise_sorry: permanent
  gap_marker: "sorry -- TODO"
key_flag: all
write_policy: read_only
owner: external
validators: []
key_files: []
"""

#: physical pages of the injected text layer (printed = physical - 2)
DT_PAGES = [""] * 7 + [
    "Lemma 1.1.2 (Additivity). The volume adds.\nProof. Sort the endpoints.\n",     # physical 8, printed 6
] + [""] * 5 + [
    "Definition 1.1.1 (Boxes). A box is a product of intervals.\n"
    "We note that every box is elementary as the book says.\n"
    "Exercise 1.1.3. Show something about boxes.\n"
    "Example 1.1.9. A worked example.\n",                                         # physical 14, printed 12
    "", "Lemma 1.2.5 (Finite additivity). Disjointness alone is not enough.\n",     # physical 16, printed 14
]

XREF = {"VersoHtml.constant": {"title": "constants", "description": "", "contents": {}},
        "VersoHtml.module": {"title": "modules", "description": "", "contents": {}}}
for _name in ("IsBox", "ex_113", "box_elementary", "helper_step", "Placed", "api_thing", "hidden_helper"):
    XREF["VersoHtml.constant"]["contents"][f"Demo.{_name}"] = [
        {"address": "/Demo/Section_1_1/", "id": f"Demo___{_name}",
         "data": {"module": "Demo.Section_1_1", "userName": f"Demo.{_name}", "private": False}}]
XREF["VersoHtml.constant"]["contents"]["Demo.lemma_125"] = [
    {"address": "/Demo/Section_1_2/", "id": "Demo___lemma_125",
     "data": {"module": "Demo.Section_1_2", "userName": "Demo.lemma_125", "private": False}}]


def make_docstring_tags_repo(root: Path, lean_1: str = DT_LEAN_1,
                             lean_2: str = DT_LEAN_2) -> tuple[str, Path, Path, Path, Path]:
    """The fixture: ``(commit, pdf, xref, pages pin, site dir)``."""
    (root / "Demo").mkdir(parents=True)
    (root / "Demo" / "Section_1_1.lean").write_text(lean_1, encoding="utf-8")
    (root / "Demo" / "Section_1_2.lean").write_text(lean_2, encoding="utf-8")
    (root / ".gitignore").write_text("_site/\n", encoding="utf-8")
    commit = git_commit(root)
    pdf = root.parent / "demo.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf, the text layer is injected\n")
    xref = root.parent / "xref.json"
    xref.write_text(json.dumps(XREF), encoding="utf-8")
    site = root / "_site"
    src = (root / "Demo" / "Section_1_1.lean").read_bytes()
    kw_off = src.find(b"example :")
    (site / "Demo" / "Section_1_1").mkdir(parents=True)
    (site / "Demo" / "Section_1_2").mkdir(parents=True)
    (site / "Demo" / "Section_1_1" / "index.html").write_text(
        "<html><body>"
        + "".join(f'<a id="Demo___{n}"></a>' for n in ("IsBox", "box_elementary", "helper_step", "Placed",
                                                      "api_thing", "hidden_helper"))
        + '<a id="Demo___ex_113"></a><a id="Demo___ex_113"></a>'          # twice: the page lacks one anchor
        + f'<span data-binding="kw-occ-Lean.Parser.Command.example-{kw_off}">example</span>'
        + "</body></html>", encoding="utf-8")
    (site / "Demo" / "Section_1_2" / "index.html").write_text(
        '<html><body><a id="Demo___lemma_125"></a></body></html>', encoding="utf-8")
    (site / "index.html").write_text("<html><body>demo</body></html>", encoding="utf-8")
    pages = sorted(({"path": str(p.relative_to(site)), "sha256": sha256(p.read_bytes()),
                     "bytes": p.stat().st_size} for p in site.rglob("*.html")), key=lambda p: p["path"])
    pin = root.parent / "site-pages.json"
    write_json(pin, {"note": "the fixture site", "source_id": "demo-site", "title": "the demo site",
                     "path": "_site", "rendered_at": "2026-09-02T00:00:00Z",
                     "site_sha256": V.site_sha256(pages), "pages": pages})
    return commit, pdf, xref, pin, site


class TestDocstringTagsExport(unittest.TestCase):
    """Two tagged Lean files, an injected text layer, an index and a two-page site."""

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="cf-export-dt-",
                                         dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
        cls.root = cls.base / "repo"
        cls.commit, cls.pdf, cls.xref, cls.pin, cls.site = make_docstring_tags_repo(cls.root)
        cls.adapter = cls.base / "adapter.yaml"
        cls.adapter.write_text(DT_ADAPTER, encoding="utf-8")
        cls.options = dict(pdf=cls.pdf, xref=cls.xref, site_pages=cls.pin, adapter_config=cls.adapter,
                           name="demo", repo_id="git.example.org/demo/demo",
                           pdf_text=PdfText(list(DT_PAGES), version="0.0-test", page_size_pt=(612.0, 792.0)),
                           source_id="demo-pdf", source_title="A Demo Book", page_offset=2)
        cls.doc, cls.report = docstring_tags.export(cls.root, cls.commit, site=cls.site, **cls.options)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_it_validates_canonically_without_a_warning(self):
        valid_and_quiet(self, self.doc)
        self.assertEqual(self.report.problems, [])

    def test_numbered_items_take_their_label_and_title_from_the_heading(self):
        items = by_id(self.doc, "items")
        self.assertEqual(items["definition-1.1.1"]["label"], "Definition 1.1.1")
        self.assertEqual(items["definition-1.1.1"]["title"], "Boxes")
        loc = by_id(self.doc, "locations")["definition-1.1.1@p014"]
        self.assertEqual(loc["target"]["selector"][0]["refinedBy"]["exact"], "Definition 1.1.1")
        self.assertEqual(loc["provenance"], "prov-pdf-text-match")

    def test_links_carry_sub_pages_notes_and_the_helper_role(self):
        links = by_id(self.doc, "links")
        key = links["exercise-1.1.3/key/Demo.ex_113/ii"]
        self.assertEqual(key["pages"], {"first": 12, "basis": "printed", "anchor": "own", "raw": "book p. 12"})
        self.assertEqual(key["note"], "the second part only")
        helper = links["lemma-1.1.2/helper/Demo.helper_step/i"]
        self.assertEqual(helper["raw_label"], "In-text proof of Lemma 1.1.2(i), book p. 6")
        self.assertEqual(by_id(self.doc, "items")["lemma-1.1.2"]["coverage"], "support_only")

    def test_passages_by_quote_and_by_neighbour(self):
        items = by_id(self.doc, "items")
        quoted = items[ids.passage_quote_id("every box is elementary")]
        self.assertEqual(quoted["id_source"], "quote_hash")
        self.assertEqual(quoted["pages"], {"first": 12, "basis": "printed", "anchor": "own", "raw": "book p. 12"})
        loc = by_id(self.doc, "locations")[quoted["id"] + "@p014"]
        self.assertEqual(loc["target"]["selector"][0]["refinedBy"]["exact"], "every box is elementary")
        placed = items[ids.passage_page_id(12, "a passage placed by its neighbour")]
        self.assertEqual(placed["relations"], {"near": ["exercise-1.1.3"]})
        self.assertEqual(placed["pages"]["anchor"], "inferred")
        self.assertEqual(placed["kind"], "definition")
        self.assertEqual(placed["kind_source"], "producer")
        self.assertEqual(by_id(self.doc, "locations")[placed["id"] + "@p014"]["x_page_basis"],
                         "heading of Exercise 1.1.3")

    def test_declarations_ids_sorry_and_the_unattributed(self):
        decls = by_id(self.doc, "declarations")
        self.assertEqual(decls["Demo.ex_113"]["sorry"], "exercise")
        self.assertEqual(decls["Demo.IsBox"]["signature"], "def IsBox (s : Nat) : Prop")
        self.assertIn("private:Demo.Section_1_1:Demo.hidden_helper", decls)
        anonymous = [d for d in decls.values() if d["anonymous"]]
        self.assertEqual(len(anonymous), 1)
        self.assertEqual(anonymous[0]["id_source"], "statement_hash")
        unattributed = by_id(self.doc, "unattributed")
        self.assertEqual(unattributed["Demo.api_thing"]["claim"], "api")
        self.assertEqual(unattributed["private:Demo.Section_1_1:Demo.hidden_helper"]["signals"], ["no docstring"])
        self.assertNotIn("Demo.ex_113", unattributed)

    def test_the_site_gives_anchors_checked_against_its_pages(self):
        dests = by_id(self.doc, "destinations")
        self.assertEqual(dests["Demo.IsBox@site"]["resolution"], "exact")
        self.assertEqual(dests["Demo.ex_113@site"]["resolution"], "fallback")   # the id occurs twice
        anonymous = next(d for d in self.doc["declarations"] if d["anonymous"])
        self.assertEqual(dests[anonymous["id"] + "@site"]["resolution"], "fallback")
        locs = by_id(self.doc, "locations")
        self.assertEqual(locs[anonymous["id"] + "@site"]["target"]["selector"][0]["refinedBy"]["type"],
                         "CssSelector")
        self.assertEqual(locs["Demo.IsBox@site"]["target"]["source"], "demo-site")
        self.assertNotIn("Demo.ex_113@site", locs)
        site = next(s for s in self.doc["sources"] if s["id"] == "demo-site")
        self.assertEqual(site["retrieved_at"], "2026-09-02T00:00:00Z")
        self.assertEqual(len(site["page_hashes"]), 3)
        self.assertTrue(self.report.counts["site"]["checked_against_pages"])
        self.assertIn("checked to occur once", by_id(self.doc, "provenance")["prov-verso-site"]["note"])

    def test_without_the_site_the_index_is_trusted_and_the_note_says_so(self):
        doc, report = docstring_tags.export(self.root, self.commit, site=None, **self.options)
        valid_and_quiet(self, doc)
        self.assertEqual(by_id(doc, "destinations")["Demo.ex_113@site"]["resolution"], "exact")
        self.assertIn("Demo.ex_113@site", by_id(doc, "locations"))
        self.assertIn("not on disk", by_id(doc, "provenance")["prov-verso-site"]["note"])
        self.assertIn("site.anchors_unverified", report.lossy)

    def test_a_site_that_is_not_the_pinned_one_is_refused(self):
        page = self.site / "index.html"
        kept = page.read_text(encoding="utf-8")
        page.write_text(kept + "<!-- changed -->", encoding="utf-8")
        try:
            with self.assertRaises(common.ExportError) as caught:
                docstring_tags.export(self.root, self.commit, site=self.site, **self.options)
            self.assertIn("not the pinned page", caught.exception.message)
        finally:
            page.write_text(kept, encoding="utf-8")

    def test_pages_the_manifest_leaves_out_are_counted_not_refused(self):
        # the producer chooses the pages it pins: a generated fragment on disk is not a refusal
        extra = self.site / "Demo" / "Section_1_1" / "_lazy" / "states" / "0123.html"
        extra.parent.mkdir(parents=True)
        extra.write_text("<html><body>a lazy state</body></html>", encoding="utf-8")
        try:
            doc, report = docstring_tags.export(self.root, self.commit, site=self.site, **self.options)
        finally:
            shutil.rmtree(self.site / "Demo" / "Section_1_1" / "_lazy")
        valid_and_quiet(self, doc)
        self.assertEqual(report.counts["site"]["html_pages_on_disk_not_pinned"], 1)
        self.assertEqual(len(by_id(doc, "sources")["demo-site"]["page_hashes"]), 3)

    def test_the_profiles_are_exactly_the_ones_the_document_uses(self):
        # destinations (site); a source's role is core, so no audit without a verdict; one anonymous example
        # takes no ordinal, so no history
        self.assertEqual(self.doc["profiles"], {"site": "1.0"})
        self.assertNotIn("changelog", self.doc)
        self.assertNotIn("graph", self.doc["profiles"])
        self.assertEqual(self.doc["sources"][0]["role"], "audited")

    def test_an_anonymous_example_that_states_an_item_keys_it_and_the_item_is_formalized(self):
        # the tag on the example says it states Example 1.1.9: a key link, and the class says so
        # (not example_only, which is for examples linked with role example)
        example = next(d for d in self.doc["declarations"] if d["anonymous"])
        self.assertEqual(example["kind"], "example")
        self.assertEqual(by_id(self.doc, "links")[f"example-1.1.9/key/{example['id']}"]["role"], "key")
        self.assertEqual(by_id(self.doc, "items")["example-1.1.9"]["coverage"], "formalized")
        self.assertEqual(self.report.counts["items"]["items_stated_by_an_anonymous_example_given_coverage_formalized"],
                         ["example-1.1.9"])
        self.assertNotIn("coverage", by_id(self.doc, "items")["definition-1.1.1"])

    def test_site_pages_writes_the_pin_the_exporter_reads(self):
        out = scratch(self) / "pages.json"
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            status = cf_main.main(["export", "site-pages", str(self.site), "--out", str(out),
                                   "--source-id", "demo-site", "--title", "the demo site",
                                   "--rendered-at", "2026-09-02T00:00:00Z"])
        self.assertEqual(status, 0, stderr.getvalue())
        written = json.loads(out.read_text(encoding="utf-8"))
        handmade = json.loads(self.pin.read_text(encoding="utf-8"))
        handmade.pop("note")
        self.assertEqual(written, handmade)          # path defaults to the directory's name, _site
        doc, report = docstring_tags.export(self.root, self.commit, site=self.site,
                                            **dict(self.options, site_pages=out))
        self.assertEqual(report.problems, [])
        self.assertEqual(record_diff(doc, self.doc), [])

    def test_site_pages_defaults_and_refusals(self):
        pin = site_pages.manifest(self.site)
        self.assertNotIn("source_id", pin)
        self.assertNotIn("title", pin)
        self.assertEqual(pin["path"], "_site")
        newest = max(p.stat().st_mtime for p in self.site.rglob("*.html"))
        self.assertEqual(pin["rendered_at"], common.utc(newest))
        empty = scratch(self) / "empty"
        empty.mkdir()
        for site, options, words in ((empty, {}, "holds no HTML page"),
                                     (self.site, {"rendered_at": "2026-09-02 00:00"}, "RFC 3339"),
                                     (empty / "absent", {}, "is not a directory")):
            with self.subTest(words=words):
                with self.assertRaises(common.ExportError) as caught:
                    site_pages.manifest(site, **options)
                self.assertIn(words, caught.exception.message)
        with self.assertRaises(CfError) as caught:
            cf_main.main(["export", "site-pages", str(self.site), "--out", str(self.site / "pages.json")])
        self.assertIn("inside the site", caught.exception.message)
        self.assertFalse((self.site / "pages.json").exists())

    def test_site_pages_exclude_leaves_generated_pages_out_and_the_exporter_counts_them(self):
        # a render that splits pages into fragments: the pin leaves them out, docstring-tags --site counts them
        lazy = self.site / "Demo" / "Section_1_1" / "_lazy"
        (lazy / "states").mkdir(parents=True)
        (lazy / "states" / "0123.html").write_text("<html><body>a lazy state</body></html>", encoding="utf-8")
        out = scratch(self) / "pages.json"
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                status = cf_main.main(["export", "site-pages", str(self.site), "--out", str(out),
                                       "--exclude", "*/_lazy/*", "--source-id", "demo-site",
                                       "--title", "the demo site", "--rendered-at", "2026-09-02T00:00:00Z"])
            doc, report = docstring_tags.export(self.root, self.commit, site=self.site,
                                                **dict(self.options, site_pages=out))
            everything = [p["path"] for p in site_pages.manifest(self.site)["pages"]]
            with self.assertRaises(common.ExportError) as caught:
                site_pages.manifest(self.site, exclude=("*.html",))
        finally:
            shutil.rmtree(lazy)
        self.assertEqual(status, 0)
        handmade = json.loads(self.pin.read_text(encoding="utf-8"))
        handmade.pop("note")
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), handmade)
        self.assertEqual(report.counts["site"]["html_pages_on_disk_not_pinned"], 1)
        self.assertEqual(record_diff(doc, self.doc), [])
        self.assertIn("Demo/Section_1_1/_lazy/states/0123.html", everything)
        self.assertIn("--exclude", caught.exception.message)

    def test_the_command_runs_end_to_end(self):
        out = scratch(self) / "demo.annotations.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), unittest.mock.patch.object(
                PdfText, "extract", classmethod(lambda cls, path: PdfText(list(DT_PAGES), "0.0-test"))):
            status = cf_main.main(["export", "docstring-tags", str(self.root), "--commit", self.commit,
                                   "--pdf", str(self.pdf), "--xref", str(self.xref), "--site-pages", str(self.pin),
                                   "--site", str(self.site), "--adapter-config", str(self.adapter),
                                   "--out", str(out), "--name", "demo", "--repo-id", "git.example.org/demo/demo",
                                   "--source-id", "demo-pdf", "--source-title", "A Demo Book",
                                   "--page-offset", "2"])
        self.assertEqual(status, 0, stderr.getvalue())
        doc = V.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(doc["companion"]["generator"], common.generator("docstring-tags"))
        self.assertNotIn("page_size", doc["sources"][0])


# ---------------------------------------------------------------------------
# the review of the exporters (review record of pull request 15): one regression test per fix
# ---------------------------------------------------------------------------

def commit_all(root: Path, message: str = "variant") -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.invalid")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True, env=env)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


def edit_json(path: Path, change) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    change(obj)
    write_json(path, obj)


def relock_ledger(root: Path) -> None:
    """After an edit of the ledger: the sha256 (and bytes) the other overlay files pin it by."""
    data = (root / "source-formalization-ledger.json").read_bytes()
    sha = sha256(data)
    overlay = root / "pdf-overlay" / "data"
    for name in ("target-refinement.json", "faithfulness-verdicts.json"):
        edit_json(overlay / name, lambda o: o.update(ledger_sha256=sha))
    edit_json(overlay / "destinations.json", lambda o: o["ledger"].update(sha256=sha, bytes=len(data)))


def at_variant(case: unittest.TestCase, mutate) -> tuple[Path, str]:
    """The synthetic overlay companion with ``mutate(root)`` applied and committed."""
    root = scratch(case) / "repo"
    root.mkdir()
    make_attribution_table_repo(root)
    mutate(root)
    return root, commit_all(root)


def at_export(root: Path, commit: str):
    return attribution_table.export(root, commit, name="demo", repo_id="git.example.org/demo/demo",
                                    source_id="demo-pdf", source_title="A Demo Book")


def geometry(root: Path, batch: str = "a") -> Path:
    return root / "pdf-overlay" / "data" / f"geometry-batch-{batch}.json"


def set_label(root: Path, item_id: str, label: str, batch: str = "a") -> None:
    def change(b):
        next(it for it in b["items"] if it["item_id"] == item_id)["label"] = label
    edit_json(geometry(root, batch), change)


class TestTheSubPartRenderer(unittest.TestCase):
    """Review item 2 and 8: one renderer, ranges and lists whole, a refused sub-part reported."""

    def test_the_shared_renderer(self):
        for text, want in (("(ii)", ("ii", None)), ("(i)(a)", ("i.a", None)), ("(iv)-(vi)", ("iv-vi", None)),
                           ("(i)–(iii)", ("i-iii", None)), ("(i),(ii)", ("i,ii", None)),
                           ("(i), (iii)", ("i,iii", None)), ("(i) and (iii)", ("i,iii", None)),
                           ("(i)/(ii)", ("i,ii", None)), (" (Finite additivity)", ("", None)),
                           ("(Finite additivity)", ("", "(Finite additivity)")),
                           # a primed part keeps its prime; a shape outside the grammar is refused
                           ("(ii')", ("ii'", None)), ("(b'')", ("b''", None)), ("(i)-(ii')", ("i-ii'", None)),
                           ("(ii*)", ("", "(ii*)")), ("(i)-(ii*)", ("", "(i)-(ii*)")),
                           (", book p. 6", ("", None)), ("", ("", None))):
            with self.subTest(text=text):
                sub, _rest, refused = common.render_sub(text)
                self.assertEqual((sub, refused), want)
                if sub:
                    self.assertRegex(sub, common.SUB_PATTERN)

    def test_both_exporters_render_one_label_alike(self):
        kinds = tuple(attribution_table.cf_items.DEFAULT_KINDS)
        for label, sub in (("Exercise 1.1.3(i)(a)", "i.a"), ("Exercise 1.1.3(i)-(iii)", "i-iii"),
                           ("Exercise 1.1.3(i), (iii)", "i,iii")):
            with self.subTest(label=label):
                rec = attribution_table.item_identity(label, "item:x", kinds)[0]
                self.assertEqual(rec["sub"], sub)
                self.assertNotIn("_trailing", rec)
                self.assertEqual(common.render_sub(common.after_number(label, "1.1.3"))[0], sub)

    def test_a_range_keeps_the_title_after_it(self):
        kinds = tuple(attribution_table.cf_items.DEFAULT_KINDS)
        rec = attribution_table.item_identity("Lemma 1.2.3(i)-(iii) (Monotonicity)", "item:x", kinds)[0]
        self.assertEqual((rec["sub"], rec["title"]), ("i-iii", "Monotonicity"))
        self.assertNotIn("_trailing", rec)


class TestAttributionTableReviewFixes(unittest.TestCase):
    """The latent paths review items 2 to 7 found, each on a variant of the synthetic overlay companion."""

    def test_a_sub_part_range_or_list_is_carried_whole(self):
        for label, iid, sub in (("Remark 1.1.4(i)-(iii)", "remark-1.1.4-i-iii", "i-iii"),
                                ("Remark 1.1.4(i), (iii)", "remark-1.1.4-i,iii", "i,iii")):
            with self.subTest(label=label):
                root, commit = at_variant(self, lambda r, label=label: set_label(r, "item:batch-b:rem-1.1.4",
                                                                                 label, "b"))
                doc, report = at_export(root, commit)
                valid_and_quiet(self, doc)
                self.assertEqual(report.problems, [])
                self.assertEqual(by_id(doc, "items")[iid]["sub"], sub)
                self.assertNotIn("item.label_trailing", report.lossy)

    def test_an_equation_title_is_the_producers(self):
        root, commit = at_variant(self, lambda r: set_label(r, "item:batch-b:rem-1.1.4",
                                                            "Equation (1.1.4): a made-up name", "b"))
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        item = by_id(doc, "items")["equation-1.1.4"]
        self.assertEqual((item["title"], item["title_source"]), ("a made-up name", "producer"))
        kinds = tuple(attribution_table.cf_items.DEFAULT_KINDS)
        bare = attribution_table.item_identity("Equation (1.1.4)", "item:x", kinds)[0]
        self.assertNotIn("title", bare)
        self.assertNotIn("title_source", bare)

    def test_a_sub_part_the_format_cannot_carry_is_dropped_and_reported(self):
        root, commit = at_variant(self, lambda r: set_label(r, "item:batch-b:rem-1.1.4", "Remark 1.1.4(ii*)", "b"))
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        self.assertNotIn("sub", by_id(doc, "items")["remark-1.1.4"])
        self.assertEqual(len(report.problems), 1)
        self.assertIn("(ii*)", report.problems[0])

    def test_a_primed_sub_part_is_carried_with_its_prime(self):
        root, commit = at_variant(self, lambda r: set_label(r, "item:batch-b:rem-1.1.4", "Remark 1.1.4(b')", "b"))
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        self.assertEqual(by_id(doc, "items")["remark-1.1.4-b'"]["sub"], "b'")
        self.assertIn("remark-1.1.4-b'/attributed/Demo.Box.volume", by_id(doc, "links"))

    def test_an_item_the_table_keys_to_an_anonymous_example_is_formalized(self):
        def mutate(root):
            def change(table):
                e = table["entries"][1]                                    # Theorem 1.1.2
                e["formalization_ids"].append("example:demo-volume-nonneg")
                e["key_formalization_ids"].append("example:demo-volume-nonneg")
            edit_json(root / "pdf-overlay" / "data" / "target-refinement.json", change)
        root, commit = at_variant(self, mutate)
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        hashed = next(d["id"] for d in doc["declarations"] if d["anonymous"] and not d.get("unlocated"))
        self.assertEqual(by_id(doc, "links")[f"theorem-1.1.2/key/{hashed}"]["role"], "key")
        self.assertEqual(by_id(doc, "items")["theorem-1.1.2"]["coverage"], "formalized")
        self.assertEqual(report.counts["items_stated_by_an_anonymous_example_given_coverage_formalized"],
                         ["theorem-1.1.2"])
        # the class is the table's where it gives one: a named key alone writes no coverage
        self.assertNotIn("coverage", by_id(doc, "items")["definition-1.1.1"])

    def test_snippets_are_cut_from_the_lean_at_the_commit(self):
        tail = ("-- `example:demo-tagged`\n"
                "example (b : Box) : b.volume = b.volume := by\n  rfl\n\n"
                "/-- Twice the volume. -/\n"
                "def Box.double (b : Box) : Nat :=\n  b.volume + b.volume\n\n"
                "/-- Too long to show whole. -/\n"
                "theorem long_statement (b : Box)\n"
                + "".join(f"    (h{k} : b.left ≤ b.right + {k})\n" for k in range(14))
                + "    : True := trivial\n\nend Demo\n")
        new = {"example:demo-tagged": "anonymous example", "Demo.Box.double": "definition",
               "Demo.long_statement": "theorem"}

        def mutate(root):
            lean = root / "Demo" / "Basic.lean"
            lean.write_text(lean.read_text(encoding="utf-8").replace("end Demo\n", tail), encoding="utf-8")
            edit_json(root / "source-formalization-ledger.json", lambda o: o["formalization_nodes"].extend(
                {"id": i, "kind": k, "module": "Demo.Basic", "file": "Demo/Basic.lean"} for i, k in new.items()))
            relock_ledger(root)
            # the ledger places example:demo-unlocated in Demo/Basic.lean, which carries no such tag
            edit_json(root / "pdf-overlay" / "data" / "target-refinement.json",
                      lambda o: o["entries"][1]["formalization_ids"].extend([*new, "example:demo-unlocated"]))
        root, commit = at_variant(self, mutate)
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        snippets = {s["declaration"]: s for s in doc["snippets"]}
        tagged = snippets[next(d["id"] for d in doc["declarations"]
                               if "example:demo-tagged" in d.get("legacy_ids", []) + [d["id"]])]
        self.assertEqual((tagged["line"], tagged["text"]), (25, "example (b : Box) : b.volume = b.volume := by"))
        self.assertEqual((snippets["Demo.Box.double"]["line"], snippets["Demo.Box.double"]["text"]),
                         (29, "def Box.double (b : Box) : Nat :=\n  b.volume + b.volume"))
        long = snippets["Demo.long_statement"]
        self.assertEqual((long["line"], long["truncated"]), (33, True))
        self.assertEqual(long["text"].split("\n")[-1], "  …")
        self.assertEqual(report.counts["snippets_truncated"], 1)
        # an id the Lean does not carry gets no snippet, and the report names it
        self.assertEqual(report.counts["snippets_unlocated"], ["example:demo-unlocated"])
        self.assertNotIn("example:demo-unlocated", snippets)

    def test_a_leftover_snippets_file_is_ignored_and_reported(self):
        def mutate(root):
            write_json(root / "pdf-overlay" / "data" / "declaration-snippets.json", {"snippets": {
                "Demo.volume_add": {"file": "Demo/Basic.lean", "line": 1, "text": "stale"}}})
        root, commit = at_variant(self, mutate)
        doc, report = at_export(root, commit)
        snippet = by_id(doc, "snippets")["snippet:Demo.volume_add"]
        self.assertEqual((snippet["line"], snippet["text"]),
                         (12, "theorem volume_add (b : Box) : b.volume = b.volume := rfl"))
        self.assertEqual(report.counts["ignored_inputs"], ["pdf-overlay/data/declaration-snippets.json"])
        self.assertEqual(report.problems, [])

    def test_a_ledger_file_outside_the_checkout_is_a_refusal(self):
        root, commit = at_variant(self, lambda r: edit_json(
            r / "source-formalization-ledger.json",
            lambda o: o["formalization_nodes"][3].update(file="../Basic.lean")))   # Demo.volume_add
        with self.assertRaises(common.ExportError) as caught:
            at_export(root, commit)
        self.assertIn("../Basic.lean", caught.exception.message)

    def test_a_formalization_id_the_ledger_lacks_is_a_refusal_naming_the_file(self):
        def mutate(root):
            edit_json(root / "pdf-overlay" / "data" / "target-refinement.json",
                      lambda o: o["entries"][1]["formalization_ids"].append("Demo.not_in_the_ledger"))
        root, commit = at_variant(self, mutate)
        with self.assertRaises(common.ExportError) as caught:
            at_export(root, commit)
        self.assertIn("target-refinement.json", caught.exception.message)
        self.assertIn("Demo.not_in_the_ledger", caught.exception.message)
        root, commit = at_variant(self, lambda r: edit_json(
            r / "pdf-overlay" / "data" / "destinations.json",
            lambda o: o["destinations"].append({"formalization_id": "Demo.gone", "resolution": "fallback",
                                                "fallback_href": "Demo/Basic/", "fallback_label": "module page"})))
        with self.assertRaises(common.ExportError) as caught:
            at_export(root, commit)
        self.assertIn("destinations.json", caught.exception.message)

    def test_a_public_field_of_a_private_structure_names_its_parent_by_id(self):
        private = "private structure Secret where\n  x : Nat\n\nend Demo\n"

        def mutate(root):
            lean = root / "Demo" / "Basic.lean"
            lean.write_text(lean.read_text(encoding="utf-8").replace("end Demo\n", private), encoding="utf-8")

            def change(ledger):
                ledger["formalization_nodes"] += [
                    {"id": "Demo.Secret.x", "kind": "structure field", "module": "Demo.Basic",
                     "file": "Demo/Basic.lean"},
                    {"id": "private:Demo.Basic:Demo/Basic.lean:L7:Secret", "kind": None, "module": "Demo.Basic",
                     "file": "Demo/Basic.lean"}]
                ledger["edges"] += [
                    {"formalization_id": "Demo.Secret.x", "role": "consumer", "source_id": "src:1.1:boxes"},
                    {"formalization_id": "private:Demo.Basic:Demo/Basic.lean:L7:Secret", "role": "consumer",
                     "source_id": "src:1.1:boxes"}]
            edit_json(root / "source-formalization-ledger.json", change)
            relock_ledger(root)
        root, commit = at_variant(self, mutate)
        doc, _report = at_export(root, commit)
        valid_and_quiet(self, doc)
        decls = by_id(doc, "declarations")
        pid = "private:Demo.Basic:Demo.Secret"
        self.assertEqual(decls["Demo.Secret.x"]["parent"], pid)
        # the private structure's own record, with the ledger's positional id, is kept, not replaced
        self.assertEqual(decls[pid]["legacy_ids"], ["private:Demo.Basic:Demo/Basic.lean:L7:Secret"])

    def test_an_item_linked_only_by_support_edges_carries_the_class_they_imply(self):
        for target, coverage in (("example:demo-volume-nonneg", "example_only"), ("Demo.volume_add", "support_only")):
            def mutate(root, target=target):
                edit_json(root / "pdf-overlay" / "data" / "target-refinement.json",
                          lambda o: o.update(entries=[e for e in o["entries"]
                                                      if e["item_id"] != "item:batch-a:rem-volume"]))

                def change(ledger):
                    for e in ledger["edges"]:
                        if e["role"] == "support" and e["source_id"] == "src:1.1:volume":
                            e["formalization_id"] = target
                edit_json(root / "source-formalization-ledger.json", change)
                relock_ledger(root)
            with self.subTest(coverage=coverage):
                root, commit = at_variant(self, mutate)
                doc, report = at_export(root, commit)
                valid_and_quiet(self, doc)
                passage = by_id(doc, "items")[ids.passage_page_id(6, "Remark on volume")]
                self.assertEqual(passage["coverage"], coverage)
                self.assertEqual(report.bent["item.coverage_from_support_edges"]["count"], 1)

    def test_an_unnumbered_passage_before_printed_page_one_is_a_refusal(self):
        def mutate(root):
            def change(b):
                next(it for it in b["items"] if it["item_id"] == "item:batch-a:rem-volume")["regions"][0]["page"] = 2
            edit_json(geometry(root), change)
        root, commit = at_variant(self, mutate)
        with self.assertRaises(common.ExportError) as caught:
            at_export(root, commit)
        self.assertIn("before printed page 1", caught.exception.message)
        self.assertIn("section 5.2", caught.exception.hint)

    def test_a_replacement_cited_by_its_full_repo_path_resolves(self):
        def mutate(root):
            edit_json(root / "pdf-overlay" / "data" / "faithfulness-verdicts.json",
                      lambda o: o["entries"][0]["declarations"][0].update(
                          note="Fixed in PR #3: replacement_stated (Demo/Basic.lean:20) states it."))
        root, commit = at_variant(self, mutate)
        doc, report = at_export(root, commit)
        self.assertEqual(by_id(doc, "verdicts")["verdict:theorem-1.1.2"]["resolution"]["replaced_by"],
                         ["Demo.replacement_stated"])
        self.assertNotIn("verdict.replaced_by_unresolved", report.lossy)

    def test_a_key_id_outside_the_entry_s_formalization_ids_is_reported(self):
        def mutate(root):
            edit_json(root / "pdf-overlay" / "data" / "target-refinement.json",
                      lambda o: o["entries"][1]["key_formalization_ids"].append("Demo.Box"))
        root, commit = at_variant(self, mutate)
        _doc, report = at_export(root, commit)
        self.assertEqual(report.lossy["link.key_outside_formalization_ids"]["examples"], ["theorem-1.1.2: Demo.Box"])

    def test_one_support_edge_through_two_single_item_nodes_is_one_link(self):
        def mutate(root):
            def batch(b):
                next(it for it in b["items"] if it["item_id"] == "item:batch-a:rem-volume")["source_ids"].append(
                    "src:1.1:volume-2")
            edit_json(geometry(root), batch)

            def change(ledger):
                ledger["source_nodes"].append({"id": "src:1.1:volume-2", "plan_item_ids": [],
                                               "status": "implemented", "semantic_status": "verified",
                                               "external_admissions": []})
                ledger["edges"].append({"formalization_id": "example:demo-volume-nonneg", "role": "support",
                                        "source_id": "src:1.1:volume-2", "realization": "implemented"})
            edit_json(root / "source-formalization-ledger.json", change)
            relock_ledger(root)
        root, commit = at_variant(self, mutate)
        doc, report = at_export(root, commit)
        valid_and_quiet(self, doc)
        self.assertEqual(report.counts["ledger_support_edges"]["same_link_from_another_node"], 1)

    def test_malformed_geometry_is_a_refusal_not_an_index_error(self):
        def three_points(root):
            def change(b):
                reg = next(it for it in b["items"] if it["item_id"] == "item:batch-a:rem-volume")["regions"][0]
                reg["geometry"]["quads"][0] = reg["geometry"]["quads"][0][:3]
            edit_json(geometry(root), change)
        for mutate, words in ((three_points, "four (x, y) points"),
                              (lambda r: set_label(r, "item:batch-a:rem-volume", "Unnumbered "), "passage title")):
            root, commit = at_variant(self, mutate)
            with self.subTest(words=words), self.assertRaises(common.ExportError) as caught:
                at_export(root, commit)
            self.assertIn(words, caught.exception.message)

    def test_the_evidence_only_bucket_is_counted_not_assumed(self):
        self.assertNotIn("verdict.replaced_by_evidence_only", TestAttributionTableExport.report.lossy)

        def mutate(root):
            edit_json(root / "pdf-overlay" / "data" / "faithfulness-verdicts.json",
                      lambda o: o["entries"][0]["evidence"].append(
                          "The same PR also added Box.volume (Demo/Basic.lean:9)."))
        root, commit = at_variant(self, mutate)
        _doc, report = at_export(root, commit)
        self.assertEqual(report.lossy["verdict.replaced_by_evidence_only"]["examples"],
                         ["theorem-1.1.2: volume (Demo/Basic.lean)"])


class TestTheConfigurableInputLayout(unittest.TestCase):
    """--data-dir, --ledger and --official-source move the three paths the convention does not fix."""

    @staticmethod
    def move(root: Path, src: str, dst: str) -> None:
        (root / dst).parent.mkdir(parents=True, exist_ok=True)
        (root / src).rename(root / dst)

    def test_the_data_directory_can_live_anywhere_in_the_checkout(self):
        root, commit = at_variant(self, lambda r: self.move(r, "pdf-overlay/data", "exports/overlay"))
        doc, report = attribution_table.export(root, commit, name="demo", repo_id="git.example.org/demo/demo",
                                               source_id="demo-pdf", source_title="A Demo Book",
                                               data_dir="exports/overlay")
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        inputs = [i["path"] for i in by_id(doc, "provenance")["prov-target-refinement"]["inputs"]]
        self.assertIn("exports/overlay/target-refinement.json", inputs)
        self.assertIn("exports/overlay/geometry-batch-a.json",
                      [i["path"] for i in by_id(doc, "provenance")["prov-geometry-a"]["inputs"]])

    def test_the_ledger_can_live_anywhere_and_the_graph_names_where(self):
        root, commit = at_variant(self, lambda r: self.move(r, "source-formalization-ledger.json",
                                                            "graph/ledger.json"))
        doc, report = attribution_table.export(root, commit, name="demo", repo_id="git.example.org/demo/demo",
                                               source_id="demo-pdf", source_title="A Demo Book",
                                               ledger="graph/ledger.json")
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        self.assertEqual(doc["companion"]["graph"]["path"], "graph/ledger.json")

    def test_the_official_source_record_can_live_anywhere(self):
        root, commit = at_variant(self, lambda r: self.move(r, "pdf-overlay/official-source.json",
                                                            "pdf-overlay/pdf.json"))
        doc, report = attribution_table.export(root, commit, name="demo", repo_id="git.example.org/demo/demo",
                                               source_id="demo-pdf", source_title="A Demo Book",
                                               official_source="pdf-overlay/pdf.json")
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        self.assertEqual(doc["sources"][0]["bytes"], 1234)

    def test_a_missing_input_names_the_flag_that_moves_it(self):
        root = scratch(self) / "repo"
        root.mkdir()
        commit = make_attribution_table_repo(root)
        for kwargs, words in (({"data_dir": "nowhere"}, "--data-dir"),
                              ({"ledger": "nowhere.json"}, "--ledger"),
                              ({"official_source": "nowhere.json"}, "--official-source")):
            with self.subTest(**kwargs), self.assertRaises(common.ExportError) as caught:
                attribution_table.export(root, commit, name="demo", repo_id="x/y/z", source_id="demo-pdf",
                                         source_title="t", **kwargs)
            self.assertIn(words, (caught.exception.hint or "") + caught.exception.message)

    def test_a_path_outside_the_checkout_is_refused(self):
        root = scratch(self) / "repo"
        root.mkdir()
        commit = make_attribution_table_repo(root)
        for value in ("/etc", "../elsewhere", "pdf-overlay/../../data", ""):
            with self.subTest(value=value), self.assertRaises(common.ExportError) as caught:
                attribution_table.export(root, commit, name="demo", repo_id="x/y/z", source_id="demo-pdf",
                                         source_title="t", data_dir=value)
            self.assertIn("--data-dir", caught.exception.message)

    def test_the_command_passes_all_three_flags_through(self):
        def mutate(r):
            self.move(r, "pdf-overlay/data", "exports/overlay")
            self.move(r, "source-formalization-ledger.json", "graph/ledger.json")
            self.move(r, "pdf-overlay/official-source.json", "exports/pdf.json")
        root, commit = at_variant(self, mutate)
        out = scratch(self) / "moved.annotations.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cf_main.main(["export", "attribution-table", str(root), "--commit", commit,
                                   "--out", str(out), "--repo-id", "git.example.org/demo/demo",
                                   "--source-id", "demo-pdf", "--source-title", "A Demo Book",
                                   "--data-dir", "exports/overlay", "--ledger", "graph/ledger.json",
                                   "--official-source", "exports/pdf.json"])
        self.assertEqual(status, 0, stderr.getvalue())
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(doc["companion"]["graph"]["path"], "graph/ledger.json")


class TestTheExportCommandReviewFixes(unittest.TestCase):
    """Review items 3 and 7: the backstop, and nothing written under the checkout."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="cf-export-cmd-",
                                         dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
        cls.commit = make_attribution_table_repo(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def argv(self, out: Path, *extra: str) -> list[str]:
        return ["export", "attribution-table", str(self.root), "--commit", self.commit, "--out", str(out),
                "--name", "demo", "--repo-id", "git.example.org/demo/demo", "--source-id", "demo-pdf",
                "--source-title", "A Demo Book", *extra]

    def test_an_unexpected_input_is_a_clean_error_not_a_traceback(self):
        out = scratch(self) / "demo.annotations.json"
        for exc in (KeyError("region:x"), ValueError("bad"), IndexError("list index out of range"),
                    subprocess.CalledProcessError(1, ["pdftotext", "x.pdf"], stderr=b"broken")):
            with self.subTest(exc=type(exc).__name__), unittest.mock.patch.object(
                    attribution_table, "export", side_effect=exc):
                with self.assertRaises(CfError) as caught:
                    cf_main.main(self.argv(out))
                self.assertTrue(caught.exception.message)
                self.assertFalse(out.exists())

    def test_out_or_report_inside_the_checkout_is_refused(self):
        outside = scratch(self) / "demo.annotations.json"
        for argv in (self.argv(self.root / "demo.annotations.json"),
                     self.argv(outside, "--report", str(self.root / "sub" / "report.json"))):
            with self.subTest(argv=argv[-1]), self.assertRaises(CfError) as caught:
                cf_main.main(argv)
            self.assertIn("inside the checkout", caught.exception.message)
        self.assertEqual(subprocess.run(["git", "-C", str(self.root), "status", "--porcelain"],
                                        capture_output=True, text=True).stdout, "")

    def test_every_export_flag_is_documented(self):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        from cfcmd import export as export_cmd
        export_cmd.register(sub)
        docs = (SKILL / "docs" / "exporters.md").read_text(encoding="utf-8")
        flags = set()
        for action in sub.choices["export"]._subparsers._group_actions[0].choices.values():
            for a in action._actions:
                flags.update(o for o in a.option_strings if o.startswith("--") and o != "--help")
        self.assertEqual(sorted(f for f in flags if f not in docs), [])


def git_commit_head(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def dt_variant(case: unittest.TestCase, lean_1: str, lean_2: str = "namespace Demo\nend Demo\n", *,
               adapter: str = DT_ADAPTER, pages=None):
    """A docstring-tags companion with these two files, exported without the site; ``(doc, report)``."""
    base = scratch(case)
    root = base / "repo"
    commit, pdf, xref, pin, _site = make_docstring_tags_repo(root, lean_1, lean_2)
    config = base / "adapter.yaml"
    config.write_text(adapter, encoding="utf-8")
    return docstring_tags.export(root, commit, pdf=pdf, xref=xref, site_pages=pin, site=None,
                                 adapter_config=config, name="demo", repo_id="git.example.org/demo/demo",
                                 pdf_text=PdfText(list(pages or DT_PAGES), version="0.0-test"),
                                 source_id="demo-pdf", source_title="A Demo Book", page_offset=2)


def dt_lean(*decls: str) -> str:
    return "namespace Demo\n\n" + "\n\n".join(decls) + "\n\nend Demo\n"


class TestDocstringTagsReviewFixes(unittest.TestCase):
    """Review items 8 to 14, each on a small docstring-tagged variant."""

    def test_a_tag_s_sub_part_range_or_list_is_carried_whole(self):
        doc, report = dt_variant(self, dt_lean(
            "/-- `Exercise 1.1.3(i)-(iii)`. -/\ntheorem a : True := trivial",
            "/-- `Exercise 1.1.3(i), (iii)`. -/\ntheorem b : True := trivial",
            "/-- `Exercise 1.1.3(i) and (iii)`. -/\ntheorem c : True := trivial",
            "/-- `Exercise 1.1.3(i)/(ii)`. -/\ntheorem d : True := trivial"))
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        self.assertEqual(sorted(ln["sub"] for ln in doc["links"]), ["i,ii", "i,iii", "i,iii", "i-iii"])

    def test_a_sub_part_outside_the_grammar_is_dropped_and_reported(self):
        doc, report = dt_variant(self, dt_lean(
            "/-- `Exercise 1.1.3(ii*)`. -/\ntheorem a : True := trivial",
            "/-- `Lemma 1.2.5(Finite additivity)`. -/\ntheorem b : True := trivial",
            "/-- `Lemma 1.2.5 (Finite additivity)`. -/\ntheorem c : True := trivial"))
        valid_and_quiet(self, doc)
        self.assertEqual([ln.get("sub") for ln in doc["links"]], [None, None, None])
        # the two attached parentheticals are refused sub-parts; the spaced one is a title
        self.assertEqual(len(report.problems), 2)
        self.assertIn("(ii*)", report.problems[0])
        self.assertIn("(Finite additivity)", report.problems[1])

    def test_a_primed_part_keeps_its_prime_and_a_two_item_tag_pairs_the_items(self):
        # the format's mapping notes, section 1: `(b')` is sub b'; a declaration that states a lemma and the
        # exercise that proves it gets two key links, and the items are the same content
        doc, report = dt_variant(self, dt_lean(
            "/-- `Lemma 1.2.5(b') / Exercise 1.1.3`. -/\ntheorem a : True := trivial",
            "/-- `Exercise 1.1.3(c'')`. -/\ntheorem b : True := trivial"))
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        links = by_id(doc, "links")
        self.assertEqual(links["lemma-1.2.5/key/Demo.a/b'"]["sub"], "b'")
        self.assertEqual(links["exercise-1.1.3/key/Demo.a"]["role"], "key")
        self.assertEqual(links["exercise-1.1.3/key/Demo.b/c''"]["sub"], "c''")
        items = by_id(doc, "items")
        self.assertEqual(items["lemma-1.2.5"]["relations"], {"same_as": ["exercise-1.1.3"]})
        self.assertEqual(items["exercise-1.1.3"]["relations"], {"same_as": ["lemma-1.2.5"]})
        self.assertEqual(report.counts["items"]["items_paired_by_one_declaration"],
                         [{"decl": "Demo.a", "items": ["exercise-1.1.3", "lemma-1.2.5"]}])
        self.assertEqual(report.counts["sub_fixes_over_adapter"], [])

    def test_an_in_text_neighbour_is_kept_beside_a_page_and_prose_is_not_a_kind(self):
        doc, report = dt_variant(self, dt_lean(
            '/-- `In-text, near Exercise 1.1.3, book p. 12` ("every box is elementary" as the book says). -/\n'
            "theorem a : True := trivial"))
        valid_and_quiet(self, doc)
        passage = next(i for i in doc["items"] if i["form"] == "passage")
        self.assertEqual(passage["relations"], {"near": ["exercise-1.1.3"]})
        self.assertEqual(passage["kind"], "passage")
        self.assertNotIn("kind_source", passage)
        self.assertEqual(passage["pages"]["anchor"], "own")

    def test_the_adapter_config_decides_how_every_tag_is_re_read(self):
        bare = DT_ADAPTER.replace("require_backticks: true", "require_backticks: false")
        doc, report = dt_variant(self, dt_lean(
            "/-- Exercise 1.1.3(ii), book p. 12 -/\ntheorem a : True := trivial",
            '/-- In-text, book p. 12: "every box is elementary" as the book says. -/\n'
            "theorem b : True := trivial"), adapter=bare)
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        key = by_id(doc, "links")["exercise-1.1.3/key/Demo.a/ii"]
        self.assertEqual(key["pages"]["first"], 12)
        passage = next(i for i in doc["items"] if i["form"] == "passage")
        self.assertEqual(passage["title"], '"every box is elementary" as the book says')

    def test_a_page_outside_the_pdf_or_reversed_is_dropped_and_names_the_declaration(self):
        doc, report = dt_variant(self, dt_lean(
            "/-- `Exercise 1.1.3`, book p. 400. -/\ntheorem far : True := trivial",
            "/-- `Exercise 1.1.3`, book pp. 12-10. -/\ntheorem back : True := trivial"))
        valid_and_quiet(self, doc)
        self.assertTrue(all("pages" not in ln for ln in doc["links"]))
        self.assertEqual(len(report.problems), 2)
        self.assertIn("Demo.far", report.problems[0])
        self.assertIn("Demo.back", report.problems[1])

    def test_a_url_in_a_note_or_a_title_is_dropped_and_reported(self):
        doc, report = dt_variant(self, dt_lean(
            "/-- `Exercise 1.1.3` (see https://example.org/errata). -/\ntheorem a : True := trivial",
            "/-- `In-text, book p. 12`, see https://example.org/x. -/\ntheorem b : True := trivial"))
        valid_and_quiet(self, doc)
        self.assertNotIn("note", by_id(doc, "links")["exercise-1.1.3/key/Demo.a"])
        passage = next(i for i in doc["items"] if i["form"] == "passage")
        self.assertEqual(passage["title"], "In-text passage formalised by Demo.b")
        self.assertEqual(len(report.problems), 2)

    def test_an_item_the_text_layer_shows_no_heading_for_takes_its_key_link_s_pages(self):
        doc, report = dt_variant(self, dt_lean(
            "/-- `Lemma 1.3.1`, book p. 12. -/\ntheorem a : True := trivial"))
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        self.assertEqual(by_id(doc, "items")["lemma-1.3.1"]["pages"],
                         {"first": 12, "basis": "printed", "anchor": "own", "raw": "book p. 12"})
        self.assertEqual(report.counts["headings"]["pages_from_key_link_items"], ["lemma-1.3.1"])

    def test_an_anonymous_declaration_only_leanparse_sees_is_reported_not_a_key_error(self):
        doc, report = dt_variant(self, dt_lean(
            'def s : String := "a\nexample : True := trivial"',
            "/-- `Exercise 1.1.3`. -/\ntheorem a : True := trivial"))
        self.assertTrue(any(p.startswith("anonymous join") for p in report.problems))
        self.assertIn("Demo.a", by_id(doc, "declarations"))

    def test_a_tagged_duplicate_declaration_is_reported_not_a_key_error(self):
        doc, report = dt_variant(self, dt_lean("/-- `Exercise 1.1.3`. -/\ntheorem a : True := trivial"),
                                 dt_lean("/-- `Lemma 1.2.5`. -/\ntheorem a : True := trivial"))
        self.assertTrue(any("duplicate declaration id Demo.a" in p for p in report.problems))
        self.assertTrue(any("does not carry" in p for p in report.problems))

    def test_a_passage_quote_is_verified_over_its_whole_page_range(self):
        doc, report = dt_variant(self, dt_lean(
            '/-- `In-text, book pp. 11-12` ("every box is elementary" as the book says). -/\n'
            "theorem a : True := trivial"))
        valid_and_quiet(self, doc)
        passage = next(i for i in doc["items"] if i["form"] == "passage")
        self.assertEqual(passage["id"], ids.passage_quote_id("every box is elementary"))
        self.assertEqual(passage["pages"], {"first": 11, "last": 12, "basis": "printed", "anchor": "own",
                                            "raw": "book pp. 11-12"})
        self.assertIn(passage["id"] + "@p014", by_id(doc, "locations"))

    def test_x_keys_are_bent_buckets_and_the_tool_names_a_user_config(self):
        pages = list(DT_PAGES)
        pages[14] = "Exercise 1.1.3. Again.\n"                     # a second heading line: two candidates
        doc, report = dt_variant(self, dt_lean(
            "/-- `In-text definition, Exercise 1.1.3` (a passage placed by its neighbour). -/\n"
            "def Placed : Prop := True"), pages=pages)
        self.assertEqual(report.bent["location.x_page_basis"]["count"], 1)
        self.assertEqual(report.bent["location.x_heading_candidates"]["count"], 1)
        tool = by_id(doc, "provenance")["prov-docstring-tag"]["tool"]
        self.assertEqual(tool["version"], "config adapter.yaml")



class TestDocstringTagsWithoutPdfOrSite(unittest.TestCase):
    """A companion whose edition the producer does not hold: a source pinned by identifiers, items by number."""

    UNPINNED = dict(name="demo", repo_id="git.example.org/demo/demo", source_id="demo-book",
                    source_title="A Demo Book", source_edition="first", source_revision="first printing, 2026",
                    source_doi="10.1000/demo-book", source_isbn=("9781234567897",))

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="cf-export-nopdf-", dir=os.environ.get("CF_TEST_TMP") or None))
        cls.root = cls.base / "repo"
        cls.commit, _pdf, _xref, _pin, _site = make_docstring_tags_repo(cls.root)
        cls.config = cls.base / "adapter.yaml"
        cls.config.write_text(DT_ADAPTER, encoding="utf-8")
        cls.doc, cls.report = docstring_tags.export(cls.root, cls.commit, adapter_config=cls.config, **cls.UNPINNED)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_it_validates_as_a_core_only_document(self):
        valid_and_quiet(self, self.doc)
        # no destinations, no verdict (a source's role is core): nothing profile-owned, so no profiles
        self.assertNotIn("profiles", self.doc)
        self.assertNotIn("destinations", self.doc)
        self.assertEqual(self.doc["sources"][0]["role"], "audited")
        self.assertEqual([p["id"] for p in self.doc["provenance"]], ["prov-docstring-tag"])
        self.assertEqual(self.report.counts["profiles"], {})

    def test_the_edition_is_a_pdf_source_pinned_by_its_identifiers(self):
        src = self.doc["sources"]
        self.assertEqual(len(src), 1)
        self.assertEqual((src[0]["kind"], src[0]["revision"], src[0]["edition"]),
                         ("pdf", "first printing, 2026", "first"))
        self.assertEqual(src[0]["identifiers"], {"doi": "10.1000/demo-book", "isbn": ["9781234567897"]})
        for absent in ("sha256", "media_type", "physical_page_count", "printed_page_offset", "text_layer"):
            self.assertNotIn(absent, src[0])
        self.assertIn("identifiers name it", src[0]["x_note"])
        self.assertIn("pinned by its identifiers", self.report.lossy["source.bytes_unpinned"]["detail"])
        self.assertEqual(self.report.lossy["source.bytes_unpinned"]["count"], 1)

    def test_without_an_identifier_the_edition_is_an_other_source(self):
        doc, report = docstring_tags.export(self.root, self.commit, adapter_config=self.config,
                                            **dict(self.UNPINNED, source_doi=None, source_isbn=()))
        valid_and_quiet(self, doc)
        src = doc["sources"][0]
        self.assertEqual((src["kind"], src["media_type"], src["revision"]),
                         ("other", "application/pdf", "first printing, 2026"))
        self.assertNotIn("identifiers", src)
        self.assertIn("`other` source", report.lossy["source.bytes_unpinned"]["detail"])
        # a PDF by medium still: a tag's page is kept on its link
        self.assertEqual(by_id(doc, "links")["exercise-1.1.3/key/Demo.ex_113/ii"]["pages"]["first"], 12)

    def test_numbered_items_are_located_by_their_number_alone(self):
        items = by_id(self.doc, "items")
        # I4 is for passages: no location, quote or pages is written for a numbered item
        for absent in ("quote", "pages"):
            self.assertNotIn(absent, items["definition-1.1.1"])
        self.assertEqual(self.doc["locations"], [])
        self.assertNotIn("item.quote_from_tag", self.report.bent)
        self.assertEqual(self.report.counts["items"]["numbered_without_own_anchor"],
                         sum(1 for i in self.doc["items"] if i["form"] == "numbered"))
        self.assertEqual(self.report.counts["items"]["unanchored_passages"], [])
        # a backticked tag's parenthetical sits outside the tag: it is the link's note, not a title
        self.assertNotIn("title", items["definition-1.1.1"])
        self.assertEqual(by_id(self.doc, "links")["definition-1.1.1/key/Demo.IsBox"]["note"], "Boxes")
        self.assertNotIn("item.title_from_tag", self.report.bent)
        self.assertIn("located by its number alone", by_id(self.doc, "provenance")["prov-docstring-tag"]["note"])

    def test_a_bare_tag_s_parenthetical_is_the_item_s_title_when_its_tags_agree(self):
        base = scratch(self)
        root = base / "repo"
        make_docstring_tags_repo(root, dt_lean(
            "/-- Definition 1.1.1 (Boxes). A box. -/\ndef IsBox : Prop := True",
            "/-- Lemma 1.1.2 (Additivity) / Exercise 1.1.4 -/\ntheorem t : True := trivial",
            "/-- Lemma 1.1.2 (Finite additivity) -/\ntheorem u : True := trivial"))
        config = base / "adapter.yaml"
        config.write_text(DT_ADAPTER.replace("require_backticks: true", "require_backticks: false"),
                          encoding="utf-8")
        doc, report = docstring_tags.export(root, git_commit_head(root), adapter_config=config, **self.UNPINNED)
        valid_and_quiet(self, doc)
        items = by_id(doc, "items")
        self.assertEqual((items["definition-1.1.1"]["title"], items["definition-1.1.1"]["title_source"]),
                         ("Boxes", "producer"))
        self.assertNotIn("title", items["lemma-1.1.2"])                       # its two tags disagree
        self.assertNotIn("title", items["exercise-1.1.4"])
        self.assertEqual(report.counts["headings"]["title_disagreements"],
                         [{"item": "lemma-1.1.2", "titles": ["Additivity", "Finite additivity"]}])
        self.assertEqual(report.bent["item.title_from_tag"]["count"], 1)

    def test_pages_are_kept_unchecked_on_a_pdf_media_type_and_a_passage_needs_a_page(self):
        links = by_id(self.doc, "links")
        self.assertEqual(links["exercise-1.1.3/key/Demo.ex_113/ii"]["pages"]["first"], 12)
        # three tags name a page: the exercise, the in-text passage and the helper
        self.assertEqual(self.report.counts["links"]["link_quote_check"], {"pages_unchecked_without_pdf": 3})
        # the in-text passage with a page is keyed by page and title; the one placed by a neighbour cannot be
        paged = by_id(self.doc, "items")[ids.passage_page_id(12, '"every box is elementary" as the book says')]
        self.assertEqual(paged["pages"], {"first": 12, "basis": "printed", "anchor": "own", "raw": "book p. 12"})
        self.assertEqual([p for p in self.report.problems if "cannot be anchored without the PDF" in p],
                         ["Demo.Placed: the in-text passage 'In-text definition, Exercise 1.1.3' cannot be "
                          "anchored without the PDF's text layer; no item was written"])

    def test_a_source_that_is_not_a_pdf_by_media_type_carries_no_pages(self):
        doc, report = docstring_tags.export(self.root, self.commit, adapter_config=self.config,
                                            **dict(self.UNPINNED, source_media_type="text/plain"))
        valid_and_quiet(self, doc)
        self.assertTrue(all("pages" not in ln for ln in doc["links"]))
        self.assertEqual(report.lossy["link.pages_without_pdf"]["count"], 3)
        self.assertFalse(any(i["form"] == "passage" for i in doc["items"]))

    def test_without_a_pdf_the_source_flags_are_required(self):
        with self.assertRaises(common.ExportError) as caught:
            docstring_tags.export(self.root, self.commit, adapter_config=self.config, name="demo",
                                  repo_id="git.example.org/demo/demo", source_id="demo-book")
        self.assertIn("--source-title, --source-revision", caught.exception.message)
        with self.assertRaises(common.ExportError):
            docstring_tags.export(self.root, self.commit, adapter_config=self.config, xref=self.base / "x",
                                  **self.UNPINNED)

    def test_the_command_runs_without_a_pdf(self):
        out = scratch(self) / "demo.annotations.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cf_main.main(["export", "docstring-tags", str(self.root), "--commit", self.commit,
                                   "--adapter-config", str(self.config), "--out", str(out), "--name", "demo",
                                   "--repo-id", "git.example.org/demo/demo", "--source-id", "demo-book",
                                   "--source-title", "A Demo Book", "--source-revision", "first printing, 2026"])
        self.assertEqual(status, 1, stderr.getvalue())        # the neighbour-placed passage is a recorded problem
        text = out.read_text(encoding="utf-8")
        self.assertEqual(V.validate(V.loads(text), text=text, canonical=True, lints=common.LINTS), [])
        self.assertIn("profiles: core only", stdout.getvalue())


class TestCompanionIdentity(unittest.TestCase):
    """companion.repo is host/org/name from the remote, never a scheme or a .git suffix."""

    def test_remote_urls(self):
        for url, want in (("https://github.com/org/name.git", "github.com/org/name"),
                          ("git@github.com:org/name.git", "github.com/org/name"),
                          ("ssh://git@host.example:2222/org/name", "host.example/org/name"),
                          ("https://user@host/org/name/", "host/org/name"),
                          ("", None), ("not a url", None)):
            with self.subTest(url=url):
                self.assertEqual(common.repo_id_from_url(url), want)

    def test_a_checkout_with_no_remote_or_an_unparsable_one_is_a_refusal(self):
        root = scratch(self) / "pinned-copy"
        root.mkdir()
        commit = make_attribution_table_repo(root)
        for remote in (None, "not a url"):
            if remote:
                subprocess.run(["git", "-C", str(root), "remote", "add", "origin", remote], check=True)
            with self.subTest(remote=remote), self.assertRaises(common.ExportError) as caught:
                attribution_table.export(root, commit, source_id="demo-pdf", source_title="A Demo Book")
            self.assertIn("no origin remote", caught.exception.message)


# ---------------------------------------------------------------------------
# reproduction against the pinned reference companions
# ---------------------------------------------------------------------------

def load_reference(case: unittest.TestCase, name: str) -> dict:
    with gzip.open(reference_file(case, f"{name}.annotations.json.gz"), "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def head_of(repo: Path) -> str:
    from cflib import gitsafe
    proc = gitsafe.run(repo, ["rev-parse", "HEAD"])
    return proc.stdout.strip() if proc.returncode == 0 else ""


def record_diff(new: dict, ref: dict) -> list[str]:
    """Every difference between two documents, one line each, records by id."""
    out: list[str] = []
    for key in sorted(set(new) | set(ref)):
        a, b = new.get(key), ref.get(key)
        if isinstance(a, list) and isinstance(b, list) and (a or b) and isinstance((a or b)[0], dict):
            am, bm = {r["id"]: r for r in a}, {r["id"]: r for r in b}
            out += [f"{key}: only exported: {i}" for i in sorted(set(am) - set(bm))]
            out += [f"{key}: only reference: {i}" for i in sorted(set(bm) - set(am))]
            for i in sorted(set(am) & set(bm)):
                if am[i] != bm[i]:
                    fields = sorted(f for f in set(am[i]) | set(bm[i]) if am[i].get(f) != bm[i].get(f))
                    out.append(f"{key}: {i} differs in {fields}")
        elif a != b:
            out.append(f"{key}: differs")
    return out


def normalise_environment(doc: dict) -> dict:
    """The one field that depends on the machine: the pdftotext version."""
    doc = copy.deepcopy(doc)
    for source in doc.get("sources", []):
        if "text_layer" in source:
            source["text_layer"]["version"] = "<environment>"
    for prov in doc.get("provenance", []):
        if prov.get("tool", {}).get("name") == "pdftotext":
            prov["tool"]["version"] = "<environment>"
    return doc


@unittest.skipUnless(env_path("CF_MT"), "set CF_MT to the measure_theory checkout at 2cd263a")
class TestMeasureTheoryReproduction(unittest.TestCase):

    def test_the_exporter_reproduces_the_reference_record_for_record(self):
        repo = env_path("CF_MT")
        if not head_of(repo).startswith(MT_COMMIT[:12]):
            self.skipTest(f"CF_MT is not at {MT_COMMIT[:12]}")
        ref = load_reference(self, "measure_theory")
        build = env_path("CF_MT_BUILD") or (repo / ".lake" / "build" / "lib" / "lean")
        doc, report = attribution_table.export(repo, MT_COMMIT, lean_build=build)
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        exported = V.canonicalize(doc)
        if not report.counts["ilean_files_read"]:
            # no build of the one module with anonymous instances: the three generated names are absent
            for d in ref["declarations"]:
                d.pop("lean_name", None)
        self.assertEqual(record_diff(exported, ref), [])
        for key in ("items", "declarations", "links", "locations", "destinations", "memberships"):
            self.assertEqual(len(exported[key]), len(ref[key]))


@unittest.skipUnless(env_path("CF_TML") and env_path("CF_TAO_PDF"),
                     "set CF_TML to the tao-measure-lean checkout at 677ada1 and CF_TAO_PDF to the published PDF")
@unittest.skipUnless(shutil.which("pdftotext"), "needs poppler's pdftotext")
class TestTaoMeasureLeanReproduction(unittest.TestCase):

    def test_the_exporter_reproduces_the_reference_record_for_record(self):
        repo, pdf = env_path("CF_TML"), env_path("CF_TAO_PDF")
        if not head_of(repo).startswith(TML_COMMIT[:12]):
            self.skipTest(f"CF_TML is not at {TML_COMMIT[:12]}")
        if sha256(pdf.read_bytes()) != PUBLISHED_SHA:
            self.skipTest("CF_TAO_PDF is not the published edition")
        site = env_path("CF_TML_SITE") or ((repo / "_site") if (repo / "_site").is_dir() else None)
        if site is None:
            self.skipTest("the rendered site is needed to reproduce the anchors; set CF_TML_SITE")
        ref = load_reference(self, "tao-measure-lean")
        xref = reference_file(self, "tml-site-xref.json.gz")
        site_pages = reference_file(self, "tml-site-pages.json")
        doc, report = docstring_tags.export(repo, TML_COMMIT, pdf=pdf, xref=xref, site_pages=site_pages, site=site)
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        self.assertEqual(record_diff(normalise_environment(V.canonicalize(doc)), normalise_environment(ref)), [])


class TestTheReferencesAreWhatTheyClaim(unittest.TestCase):
    """The maintainer's references validate canonically and carry the deliberate differences.

    Skipped unless CF_M3_REFERENCE_DIR names them: they are derived from the private companions.
    """

    def test_both_references_validate_without_a_warning(self):
        for name in ("measure_theory", "tao-measure-lean"):
            with self.subTest(companion=name):
                ref = load_reference(self, name)
                text = V.canonical_dumps(ref)
                report = V.check(ref, text=text, canonical=True)
                self.assertEqual(report.problems, [])
                self.assertEqual(report.warnings, [])
                self.assertTrue(ref["companion"]["generator"].startswith("cf export "))
                self.assertEqual(ref["sources"][0]["id"], "tao-measure-gsm126-published")

    def test_the_reference_counts_are_the_documented_ones(self):
        mt, tml = load_reference(self, "measure_theory"), load_reference(self, "tao-measure-lean")
        self.assertEqual({k: len(mt[k]) for k in ("items", "declarations", "links", "locations", "destinations",
                                                   "memberships", "artifacts", "snippets", "verdicts")},
                         {"items": 432, "declarations": 3552, "links": 1066, "locations": 509,
                          "destinations": 3181, "memberships": 5393, "artifacts": 61, "snippets": 897,
                          "verdicts": 11})
        self.assertEqual({k: len(tml[k]) for k in ("items", "declarations", "links", "locations", "destinations",
                                                    "unattributed")},
                         {"items": 651, "declarations": 1544, "links": 1169, "locations": 2201,
                          "destinations": 1544, "unattributed": 377})
        self.assertEqual(len(DELIBERATE), 4)


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
