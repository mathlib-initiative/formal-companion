"""The annotation exporters: synthetic companions everywhere, the reference companions when named.

New code, testing scripts/annotations/export/ and `cf export`
(docs/proposals/pdf-to-companion.md section 5.4, milestone M3).  Two layers:

* **unit tests** over two synthetic companions built in a temporary git
  repository: a tiny attribution table (two geometry batches, a refinement, a
  ledger, snippets, destinations, one verdict) and two docstring-tagged Lean
  files with an injected PDF text layer, a cross-reference index and a
  two-page site.  Always run.
* **reproduction tests** against the pinned reference companions, skipped
  unless the environment names them:

      CF_MT        a measure_theory checkout at 00a2438
      CF_MT_BUILD  its Lake build's lib/lean directory (default: <CF_MT>/.lake/build/lib/lean);
                   without the .ilean of the one module with anonymous instances, three
                   declarations lack their generated ``lean_name`` and the test says so
      CF_TML       a tao-measure-lean checkout at f158509
      CF_TML_SITE  its rendered literate site (default: <CF_TML>/_site when present)
      CF_TAO_PDF   the published edition's PDF (sha256 64498329...)

  Each exporter must reproduce tests/data/m3/<companion>.annotations.json.gz
  record for record.  The references are the exporters' own output, regenerated
  when a difference was deliberate; :data:`DELIBERATE` records what changed
  against the prototype documents docs/annotations.md section 10 was written
  from, and the one environment-dependent field (the pdftotext version) is
  compared after normalisation.
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
from annotations import ids                                        # noqa: E402
from annotations import validate as V                              # noqa: E402
from annotations.export import attribution_table, common, docstring_tags  # noqa: E402
from annotations.export.pdftext import PdfText                     # noqa: E402
from cflib.errors import CfError                                   # noqa: E402

DATA = SKILL / "tests" / "data" / "m3"
MT_COMMIT = "00a24385c4f6ae24e80d9fc3ac05b45dbd5ea448"
TML_COMMIT = "f1585095dc91cf04c0a67b17de0b0f13351ced10"
PUBLISHED_SHA = "64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec"

#: What the shipped references changed against the prototype documents, deliberately.
DELIBERATE = (
    "companion.generator names `cf export <convention>` and the skill's version",
    "the PDF source is named through the registry: one id (tao-measure-gsm126-published), title and edition "
    "for both companions, where the prototypes wrote tao-measure-pdf and tao-gsm126-published; the ledger's "
    "own words for the edition stay on the measure_theory source as x_companion_edition",
    "the site source's title and retrieved_at come from the page manifest, not from the build's file times",
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
    """The document validates with --canonical semantics and raises no warning."""
    text = V.canonical_dumps(V.canonicalize(doc))
    report = V.check(V.loads(text), text=text, canonical=True)
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
        "schema": "x/target-refinement", "schema_version": 1, "method": "agent_semantic_curation",
        "ledger_sha256": ledger_sha,
        "provenance": {"confirmed_by_human": False, "nodes_curated": 2,
                       "note": "Drafted by one curator agent. Batch b (section 1.1, 5 items in 3 source nodes) "
                               "was curated the same way. 3 items carry entries; 2 carry none: 1 are formalised "
                               "only by anonymous examples, Exercise 1.1.3 by nothing. Nothing else."},
        "entries": [
            {"region_id": "region:batch-a:p007:def-1.1.1:01", "item_id": "item:batch-a:def-1.1.1",
             "formalization_ids": ["Demo.Box", "Demo.Box.left", "Demo.Box.volume"],
             "key_formalization_ids": ["Demo.Box", "Demo.Box.left"]},
            {"region_id": "region:batch-a:p008:thm-1.1.2:01", "item_id": "item:batch-a:thm-1.1.2",
             "formalization_ids": ["Demo.volume_add"], "key_formalization_ids": ["Demo.volume_add"]},
            {"region_id": "region:batch-a:p008:rem-volume:01", "item_id": "item:batch-a:rem-volume",
             "formalization_ids": ["Demo.Box.volume"], "key_formalization_ids": ["Demo.Box.volume"]},
        ]})
    write_json(data / "declaration-snippets.json", {
        "schema": "x/declaration-snippets", "schema_version": 1, "missing": [], "snippets": {
            "Demo.Box": {"file": "Demo/Basic.lean", "line": 4, "text": "structure Box where"},
            "Demo.Box.left": {"file": "Demo/Basic.lean", "line": 5, "text": "left : Nat"},
            "Demo.volume_add": {"file": "Demo/Basic.lean", "line": 11,
                                "text": "theorem volume_add (b : Box) : b.volume = b.volume …"}}})
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

    def test_fallback_items_and_the_coverage_the_note_names(self):
        items = by_id(self.doc, "items")
        self.assertTrue(items["exercise-1.1.3"]["cluster_fallback"])
        self.assertEqual(items["exercise-1.1.3"]["coverage"], "not_formalized")
        self.assertEqual(items["remark-1.1.4"]["coverage"], "example_only")
        self.assertTrue(items["equation-1.1"]["cluster_fallback"])
        self.assertNotIn("coverage", items["equation-1.1"])
        self.assertEqual(self.report.bent["item.coverage_from_prose"]["count"], 2)
        self.assertEqual(self.report.lossy["item.coverage_unclassified"]["count"], 1)   # equation-1.1

    def test_the_verdict_carries_its_resolution_from_prose_and_git(self):
        verdict = by_id(self.doc, "verdicts")["verdict:theorem-1.1.2"]
        self.assertEqual(verdict["sub"], "ii")
        self.assertEqual(verdict["ledger_verdict"], "ATTRIBUTION_GAP")
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
        self.assertTrue(by_id(self.doc, "snippets")["snippet:Demo.volume_add"]["truncated"])
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


class TestCoverageFromNote(unittest.TestCase):
    """The refinement note's per-item classes, parsed rather than typed in."""

    NOTE = ("Batch d (section 1.7, 49 items in 9 source nodes) was curated on 2026-09-25. 16 items carry "
            "entries; 33 carry none: 29 are formalised only by anonymous examples, Remark 1.7.22 by nothing, "
            "and Remark 1.7.4, Definition 1.7.7 and Definition 1.7.10 have one named declaration that only "
            "supports them (the table has no support-only form). Batches a to c: the rest.")

    def test_it_reads_the_section_the_count_and_the_named_classes(self):
        section, named, total = attribution_table.coverage_from_note(self.NOTE)
        self.assertEqual((section, total), ("1.7", 33))
        self.assertEqual(named, {"remark-1.7.22": "not_formalized", "remark-1.7.4": "support_only",
                                 "definition-1.7.7": "support_only", "definition-1.7.10": "support_only"})

    def test_a_note_that_does_not_say_gives_nothing(self):
        self.assertIsNone(attribution_table.coverage_from_note("Drafted by one curator agent."))


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


def make_docstring_tags_repo(root: Path) -> tuple[str, Path, Path, Path, Path]:
    """The fixture: ``(commit, pdf, xref, pages pin, site dir)``."""
    (root / "Demo").mkdir(parents=True)
    (root / "Demo" / "Section_1_1.lean").write_text(DT_LEAN_1, encoding="utf-8")
    (root / "Demo" / "Section_1_2.lean").write_text(DT_LEAN_2, encoding="utf-8")
    (root / ".gitignore").write_text("_site/\n", encoding="utf-8")
    commit = git_commit(root)
    pdf = root.parent / "demo.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf, the text layer is injected\n")
    xref = root.parent / "xref.json"
    xref.write_text(json.dumps(XREF), encoding="utf-8")
    site = root / "_site"
    src = (root / "Demo" / "Section_1_1.lean").read_bytes()
    kw_off = src.index(b"example :")
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


# ---------------------------------------------------------------------------
# reproduction against the pinned reference companions
# ---------------------------------------------------------------------------

def load_reference(name: str) -> dict:
    with gzip.open(DATA / f"{name}.annotations.json.gz", "rb") as fh:
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


@unittest.skipUnless(env_path("CF_MT"), "set CF_MT to the measure_theory checkout at 00a2438")
class TestMeasureTheoryReproduction(unittest.TestCase):

    def test_the_exporter_reproduces_the_reference_record_for_record(self):
        repo = env_path("CF_MT")
        if not head_of(repo).startswith(MT_COMMIT[:12]):
            self.skipTest(f"CF_MT is not at {MT_COMMIT[:12]}")
        build = env_path("CF_MT_BUILD") or (repo / ".lake" / "build" / "lib" / "lean")
        doc, report = attribution_table.export(repo, MT_COMMIT, lean_build=build)
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        ref = load_reference("measure_theory")
        exported = V.canonicalize(doc)
        if not report.counts["ilean_files_read"]:
            # no build of the one module with anonymous instances: the three generated names are absent
            for d in ref["declarations"]:
                d.pop("lean_name", None)
        self.assertEqual(record_diff(exported, ref), [])
        for key in ("items", "declarations", "links", "locations", "destinations", "memberships"):
            self.assertEqual(len(exported[key]), len(ref[key]))


@unittest.skipUnless(env_path("CF_TML") and env_path("CF_TAO_PDF"),
                     "set CF_TML to the tao-measure-lean checkout at f158509 and CF_TAO_PDF to the published PDF")
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
        doc, report = docstring_tags.export(repo, TML_COMMIT, pdf=pdf, xref=DATA / "tml-site-xref.json.gz",
                                            site_pages=DATA / "tml-site-pages.json", site=site)
        valid_and_quiet(self, doc)
        self.assertEqual(report.problems, [])
        ref = load_reference("tao-measure-lean")
        self.assertEqual(record_diff(normalise_environment(V.canonicalize(doc)), normalise_environment(ref)), [])


class TestTheReferencesAreWhatTheyClaim(unittest.TestCase):
    """The shipped references validate canonically and carry the deliberate differences."""

    def test_both_references_validate_without_a_warning(self):
        for name in ("measure_theory", "tao-measure-lean"):
            with self.subTest(companion=name):
                ref = load_reference(name)
                text = V.canonical_dumps(ref)
                report = V.check(ref, text=text, canonical=True)
                self.assertEqual(report.problems, [])
                self.assertEqual(report.warnings, [])
                self.assertTrue(ref["companion"]["generator"].startswith("cf export "))
                self.assertEqual(ref["sources"][0]["id"], "tao-measure-gsm126-published")

    def test_the_reference_counts_are_the_documented_ones(self):
        mt, tml = load_reference("measure_theory"), load_reference("tao-measure-lean")
        self.assertEqual({k: len(mt[k]) for k in ("items", "declarations", "links", "locations", "destinations",
                                                   "memberships", "artifacts", "snippets", "verdicts")},
                         {"items": 432, "declarations": 3565, "links": 627, "locations": 509,
                          "destinations": 2884, "memberships": 5093, "artifacts": 61, "snippets": 542,
                          "verdicts": 11})
        self.assertEqual({k: len(tml[k]) for k in ("items", "declarations", "links", "locations", "destinations",
                                                    "unattributed")},
                         {"items": 651, "declarations": 1544, "links": 1169, "locations": 2201,
                          "destinations": 1544, "unattributed": 377})
        self.assertEqual(len(DELIBERATE), 3)


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
