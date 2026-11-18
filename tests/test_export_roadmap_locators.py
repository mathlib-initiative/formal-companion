"""`cf export roadmap-locators`: a roadmap's declarations boxed on the lines of a TeX manuscript.

New code, testing scripts/annotations/export/roadmap_locators.py and the lines of
`cf export`, `overlay.export` and `common.report_warnings` that come with it
(docs/exporters.md section 9).

The fixture is tests/_roadmap_fixture.py's synthetic companion: a manuscript of
twenty-four lines, a Lean file, three locator files and five roadmap pages, in a
git repository made in a temporary directory.  Most tests hand the exporter the
manuscript's rows directly, in place of `pdftotext`, and hide `pdftoppm`, so they
run on a machine without poppler; the last two classes run the real programs on a
PDF that prints those rows, and skip without them.
"""
from __future__ import annotations

# The first import, before anything reads HOME (see tests/_hermetic.py).
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import argparse
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
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _roadmap_fixture as fx                                      # noqa: E402
import cf as cf_main                                               # noqa: E402
from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V             # noqa: E402
from annotations.export import common, roadmap_locators, texplace  # noqa: E402
from overlay import export as overlay_export                       # noqa: E402
from overlay import shards                                         # noqa: E402

NS = shards.NAMESPACE
PDF_URL = "https://example.org/papers/pebbles.pdf"
REPO_ID = "example.org/org/pebbles"
TEX_SHA256 = hashlib.sha256(fx.SOURCE.encode("utf-8")).hexdigest()
POPPLER = shutil.which("pdftotext") is not None and shutil.which("pdftoppm") is not None
SHARED = "p001-r003-004"


def scratch(case: unittest.TestCase) -> Path:
    root = Path(tempfile.mkdtemp(prefix="cf-roadmap-", dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, root, ignore_errors=True)
    return root


def git(root: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.invalid",
               GIT_AUTHOR_DATE="2026-09-01T12:00:00Z", GIT_COMMITTER_DATE="2026-09-01T12:00:00Z",
               GIT_CONFIG_NOSYSTEM="1")
    done = subprocess.run(["git", "-C", str(root), *args], env=env, capture_output=True, text=True)
    if done.returncode != 0:                                 # pragma: no cover - a broken fixture
        raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout.strip()


class Fixture:
    """The companion at a commit, and a PDF that prints the manuscript's rows."""

    def __init__(self, root: Path, *, files: dict[str, str | None] | None = None,
                 pin: str | None = TEX_SHA256, rows: list[str] | None = None):
        self.root, self.repo, self.rows = root, root / "repo", rows
        self.repo.mkdir()
        written = fx.repository_files(pin)
        written.update(files or {})
        for rel, text in written.items():
            if text is None:
                continue
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(self.repo, "init", "-q")
        self.head = self.commit("the companion")
        self.pdf = root / "pebbles.pdf"
        self.pdf.write_bytes(fx.pdf_bytes(rows))

    def commit(self, message: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def export(self, **options):
        """The export with the manuscript's rows in place of poppler's reading of the PDF."""
        rows = self.rows
        extract = classmethod(lambda cls, pdf: texplace.Layout(fx.layout_pages(rows)))
        arguments = dict(pdf=self.pdf, pdf_url=PDF_URL, repo_id=REPO_ID, source_id="pebble-notes",
                         source_title="Pebbles", page_offset=0)
        arguments.update(options)
        with unittest.mock.patch.object(texplace.Layout, "extract", extract), \
                unittest.mock.patch.object(roadmap_locators.shutil, "which", return_value=None):
            return roadmap_locators.export(self.repo, self.head, **arguments)


def check(document: dict):
    text = V.canonical_dumps(V.canonicalize(json.loads(json.dumps(document))))
    return V.check(V.loads(text), text=text, canonical=True, lints=common.LINTS)


# ---------------------------------------------------------------------------
# reading the convention
# ---------------------------------------------------------------------------

class TestReadingALocatorSection(unittest.TestCase):
    def setUp(self):
        self.source = texplace.Source(fx.SOURCE)

    def spans(self, body: str, labels_only: bool = False):
        return roadmap_locators.section_spans(body, self.source, labels_only)

    def test_the_first_sentence_states_the_passage(self):
        self.assertEqual(self.spans("`lem:heaps`, lines 8-11: the statement (lines 9-10) and more.\n"),
                         ([(8, 11)], "head"))
        self.assertEqual(self.spans("Lines 13-17: the comparisons, and their count (see line 4).\n"),
                         ([(13, 17)], "head"))

    def test_two_ranges_joined_by_and_and_a_parenthesis_between_them(self):
        self.assertEqual(self.spans("Section 1, lines 4-6 (`sec:pebbles`) and 18-19 (the end): both.\n"),
                         ([(4, 6), (18, 19)], "head"))

    def test_a_single_line_and_an_en_dash(self):
        self.assertEqual(self.spans("Section 1, line 10: one word.\n"), ([(10, 10)], "head"))
        self.assertEqual(self.spans("lines 4–6: the colours.\n"), ([(4, 6)], "head"))

    def test_ranges_after_the_colon_are_read_when_the_head_has_none(self):
        self.assertEqual(self.spans("The steps: lines 4-5 (first), lines 6-6 (second).\n"),
                         ([(4, 6)], "paragraph"))

    def test_a_label_is_read_when_no_line_is_given(self):
        self.assertEqual(self.spans("`lem:heaps`, Section 1: the lemma.\n"), ([(8, 11)], "label"))
        self.assertEqual(self.spans("`lem:heaps`, lines 4-6: ignored in a file of labels.\n", True),
                         ([(8, 11)], "label"))

    def test_a_later_paragraph_is_the_last_resort(self):
        self.assertEqual(self.spans("A note on the figure.\n\nIt is drawn from lines 22-23.\n"),
                         ([(22, 23)], "section"))

    def test_a_section_that_names_nothing_of_the_file(self):
        self.assertEqual(self.spans("The script that draws the figure.\n"), ([], None))
        self.assertEqual(self.spans("lines 80-90: past the end of the file.\n"), ([], None))
        self.assertEqual(self.spans("`lem:absent`: no such label.\n", True), ([], None))


class TestReadingAPage(unittest.TestCase):
    def test_the_front_matter_and_the_sources(self):
        text = fx.page("A title", lean="A.b, A.c", origin="cited",
                       sources=["../../sources/x-locators.md#one", "../../sources/y.md#two"])
        self.assertEqual(roadmap_locators.front_matter(text)["lean"], "A.b, A.c")
        self.assertEqual(roadmap_locators.front_matter(text)["origin"], "cited")
        self.assertEqual(roadmap_locators.source_links(text),
                         ["../../sources/x-locators.md#one", "../../sources/y.md#two"])

    def test_a_page_with_no_front_matter_or_no_sources(self):
        self.assertEqual(roadmap_locators.front_matter("# A page\n\nText.\n"), {})
        self.assertEqual(roadmap_locators.source_links("# A page\n\n## Depends on\n\n- [x](y.md#z)\n"), [])


class TestTheModuleOfAFile(unittest.TestCase):
    def test_it_starts_where_the_librarys_root_file_stands(self):
        tracked = frozenset({"Pebbles.lean", "Pebbles/Basic.lean", "extra/Sand.lean",
                             "extra/Sand/Grain.lean", "loose/Odd/File.lean"})
        self.assertEqual(roadmap_locators.module_of("Pebbles/Basic.lean", tracked), "Pebbles.Basic")
        self.assertEqual(roadmap_locators.module_of("extra/Sand/Grain.lean", tracked), "Sand.Grain")
        self.assertEqual(roadmap_locators.module_of("loose/Odd/File.lean", tracked), "loose.Odd.File")


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------

class TestTheDocument(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="cf-roadmap-", dir=os.environ.get("CF_TEST_TMP") or None))
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.fixture = Fixture(cls.root)
        cls.document, cls.report = cls.fixture.export()
        cls.items = {item["id"]: item for item in cls.document["items"]}

    def test_it_validates(self):
        checked = check(self.document)
        self.assertEqual([str(p) for p in checked.problems], [])
        self.assertEqual(self.report.problems, [])

    def test_one_passage_per_cited_section_in_the_manuscripts_order(self):
        self.assertEqual([item["id"] for item in self.document["items"]],
                         ["notes:pebbles", "labels:lemma-1", "notes:heaps-lemma", "notes:weighing",
                          "notes:conclusion"])
        heaps = self.items["notes:heaps-lemma"]
        self.assertEqual({k: heaps[k] for k in ("id_source", "form", "kind", "title", "section", "source")},
                         {"id_source": "declared", "form": "passage", "kind": "passage",
                          "title": "Heaps lemma", "section": "1", "source": "pebble-notes"})

    def test_a_cited_page_gives_key_links_and_any_other_support(self):
        links = {(l["item"], l["declaration"]): l["role"] for l in self.document["links"]}
        self.assertEqual(links, {
            ("notes:pebbles", "Pebbles.Colour"): "key",
            ("notes:pebbles", "Pebbles.largest_redΔ"): "key",
            ("labels:lemma-1", "Pebbles.heaviest_exists"): "key",
            ("labels:lemma-1", "Pebbles.Heap"): "key",
            ("notes:heaps-lemma", "Pebbles.heaviest_exists"): "key",
            ("notes:heaps-lemma", "Pebbles.Heap"): "key",
            ("notes:weighing", "Pebbles.weigh_le"): "support",
            ("notes:conclusion", "Pebbles.weigh_le"): "support",
        })
        self.assertTrue(all("relation" not in link for link in self.document["links"]))
        self.assertEqual({rid: item.get("coverage") for rid, item in self.items.items()},
                         {"notes:pebbles": "formalized", "labels:lemma-1": "formalized",
                          "notes:heaps-lemma": "formalized", "notes:weighing": "support_only",
                          "notes:conclusion": "support_only"})

    def test_a_retracted_page_links_nothing(self):
        self.assertEqual(self.report.counts["pages_retracted"], 1)
        self.assertNotIn(("notes:pebbles", "Pebbles.weigh_le"),
                         {(l["item"], l["declaration"]) for l in self.document["links"]})

    def test_each_declaration_is_found_in_the_lean_with_its_statement(self):
        declarations = {d["id"]: d for d in self.document["declarations"]}
        self.assertEqual({name: d["line"] for name, d in declarations.items()}, fx.LEAN_LINES)
        theorem = declarations["Pebbles.heaviest_exists"]
        self.assertEqual((theorem["kind"], theorem["module"], theorem["file"]),
                         ("theorem", "Pebbles.Basic", "Pebbles/Basic.lean"))
        self.assertEqual(declarations["Pebbles.Colour"]["kind"], "inductive")
        snippets = {s["declaration"]: s for s in self.document["snippets"]}
        self.assertEqual(snippets["Pebbles.heaviest_exists"]["text"],
                         "theorem heaviest_exists (h : Heap) :\n"
                         "    ∃ w ∈ h.weights, ∀ v ∈ h.weights, v ≤ w := by")
        self.assertEqual(snippets["Pebbles.Heap"]["text"],
                         "structure Heap where\n  weights : List Nat\n  nonempty : weights ≠ []")
        destinations = {d["declaration"]: d for d in self.document["destinations"]}
        self.assertEqual(destinations["Pebbles.weigh_le"], {
            "id": "other:Pebbles.weigh_le", "declaration": "Pebbles.weigh_le", "site": "other",
            "href": f"https://{REPO_ID}/blob/{self.fixture.head}/Pebbles/Basic.lean",
            "resolution": "exact", "fragment": "L19"})

    def test_names_it_cannot_link_are_reported_and_not_linked(self):
        counts = self.report.counts
        self.assertEqual(counts["declarations_unresolved"], ["Pebbles.Heap.weights", "Pebbles.gone"])
        self.assertEqual(counts["declarations_private"], ["Pebbles.hidden_step"])
        self.assertEqual(counts["declarations_ambiguous"], [])
        self.assertEqual(sorted(self.report.lossy), ["link.private", "link.unresolved", "passage.no_lines"])

    def test_the_boxes_are_the_rows_the_lines_print_on(self):
        located = {l["id"]: l for l in self.document["locations"]}
        self.assertEqual(sorted(located), ["p001-r001-002", SHARED, "p001-r005-007", "p001-r008-008"])
        selector = located["p001-r005-007"]["target"]["selector"][0]
        self.assertEqual((selector["type"], selector["page"]), ("PdfRegionSelector", 1))
        self.assertEqual(selector["coordinate_space"]["page_box"], "CropBox")
        rect = selector["rect"]
        self.assertAlmostEqual(rect["y0"] * 792, fx.row_top(5) - 2, places=2)
        self.assertAlmostEqual(rect["y1"] * 792, fx.row_top(8) - 2, places=2)
        self.assertEqual(located["p001-r005-007"]["items"], ["notes:weighing"])

    def test_two_sections_of_the_same_lines_share_one_location(self):
        located = {l["id"]: l for l in self.document["locations"]}
        self.assertEqual(located[SHARED]["items"], ["labels:lemma-1", "notes:heaps-lemma"])
        self.assertNotIn("x_item_order", located[SHARED])

    def test_the_source_is_the_pdf_at_its_url(self):
        (source,) = self.document["sources"]
        self.assertEqual({k: source[k] for k in ("id", "kind", "title", "url", "physical_page_count",
                                                 "printed_page_offset")},
                         {"id": "pebble-notes", "kind": "pdf", "title": "Pebbles", "url": PDF_URL,
                          "physical_page_count": 1, "printed_page_offset": 0})
        self.assertEqual(source["sha256"], hashlib.sha256(self.fixture.pdf.read_bytes()).hexdigest())
        self.assertEqual(self.document["companion"]["repo"], REPO_ID)
        self.assertEqual(self.document["companion"]["commit"], self.fixture.head)

    def test_the_report_says_what_was_read_and_how_it_was_placed(self):
        counts = self.report.counts
        self.assertEqual(counts["pages"], 5)
        self.assertEqual(counts["sections_cited"], 6)
        self.assertEqual(counts["spans_read_from"], {"head": 4, "label": 1})
        self.assertEqual(counts["spans_placed_by"], {"words": 5})
        self.assertEqual(counts["word_agreement"], 1.0)
        self.assertEqual(counts["sections_naming_no_lines"],
                         ["blueprint/sources/notes-locators.md#generator"])
        self.assertEqual(counts["links_to_files_that_read_no_tex"], 1)
        self.assertEqual(counts["pages_naming_no_declaration"], 1)
        self.assertEqual((counts["boxes"], counts["box_edges_checked"], counts["box_edges_on_ink"],
                          counts["boxes_crossing"], counts["passages_unplaced"]), (4, False, [], [], []))
        self.assertEqual(counts["links_by_role"], {"key": 6, "support": 2})
        self.assertEqual(counts["tex"], {"path": fx.TEX_NAME, "sha256": TEX_SHA256, "lines": 25})
        self.assertIn("location.edges_unchecked", self.report.bent)

    def test_the_inputs_are_the_files_it_read(self):
        paths = [row["path"] for row in self.report.counts["inputs"]]
        self.assertEqual(paths, sorted(paths))
        self.assertIn(fx.TEX_NAME, paths)
        self.assertIn("blueprint/sources/notes-locators.md", paths)
        self.assertIn("blueprint/roadmap/heaps/heaviest.md", paths)
        self.assertNotIn("blueprint/roadmap/README.md", paths)
        (provenance,) = self.document["provenance"]
        self.assertEqual((provenance["actor"], provenance["method"]), ("author", "curation"))

    def test_the_warnings_name_what_the_user_must_see(self):
        warnings = common.report_warnings(self.report.counts)
        self.assertEqual(len(warnings), 3)
        self.assertIn("Pebbles.Heap.weights", warnings[0])
        self.assertIn("#generator", warnings[1])
        self.assertIn("pdftoppm", warnings[2])


class TestTheOverlayReadsIt(unittest.TestCase):
    def test_the_shared_box_is_one_region_of_two_items(self):
        document, _ = Fixture(scratch(self)).export()
        conversion = shards.convert(document)
        self.assertEqual(conversion.problems, [])
        regions = {}
        for record in conversion.records:
            for end in record["subject"]:
                regions.setdefault(end["extensions"][NS]["region"], set()).add(record["id"])
        self.assertEqual(regions[SHARED], {"labels:lemma-1", "notes:heaps-lemma"})
        shared = next(end for record in conversion.records for end in record["subject"]
                      if end["extensions"][NS]["region"] == SHARED)
        self.assertEqual(shared["extensions"][NS]["region_items"], ["labels:lemma-1", "notes:heaps-lemma"])
        self.assertEqual(shards.scan_problems(shards.file_bytes(conversion)), [])
        self.assertEqual(sorted(conversion.sections), ["1"])

    def test_it_is_one_of_the_conventions_cf_overlay_export_runs(self):
        self.assertIn("roadmap-locators", overlay_export.CONVENTIONS)
        self.assertNotIn("roadmap-locators", overlay_export.FOREIGN_COMPANION)


# ---------------------------------------------------------------------------
# what it refuses, and what it bends
# ---------------------------------------------------------------------------

class TestWhatItRefuses(unittest.TestCase):
    def refused(self, fixture: Fixture, needle: str, **options) -> common.ExportError:
        with self.assertRaises(common.ExportError) as caught:
            fixture.export(**options)
        self.assertIn(needle, caught.exception.message)
        return caught.exception

    def test_line_numbers_of_another_revision(self):
        error = self.refused(Fixture(scratch(self), pin="0" * 64), "gives line numbers of 000000000000")
        self.assertIn("--tex", error.hint)

    def test_locators_that_read_two_tex_files(self):
        labels = fx.LOCATOR_LABELS.replace(fx.TEX_NAME, "other.tex")
        self.refused(Fixture(scratch(self), files={"blueprint/sources/labels.md": labels}),
                     "more than one TeX file")

    def test_locators_that_read_no_tex_file(self):
        files = {"blueprint/sources/notes-locators.md": fx.LOCATOR_NOTES, "blueprint/sources/labels.md": None}
        self.refused(Fixture(scratch(self), files=files), "says which TeX file it reads")

    def test_a_tex_file_the_checkout_does_not_track(self):
        self.refused(Fixture(scratch(self)), "paper/notes.tex is not a tracked file", tex="paper/notes.tex")
        with self.assertRaises(common.ExportError):
            Fixture(scratch(self)).export(tex="../notes.tex")

    def test_a_pdf_that_prints_other_words(self):
        rows = ["An unrelated page about harbours and tides", "with nothing of the manuscript on it at all"]
        error = self.refused(Fixture(scratch(self), rows=rows), "it is not read as that file's PDF")
        self.assertIn("prints none of the words", error.message)

    def test_a_blueprint_that_is_not_there(self):
        self.refused(Fixture(scratch(self)), "holds no locator file", blueprint="docs/blueprint")

    def test_a_url_the_overlay_could_not_fetch(self):
        self.refused(Fixture(scratch(self)), "--pdf-url", pdf_url="http://example.org/pebbles.pdf")


class TestWhatItBendsAndReports(unittest.TestCase):
    def test_a_tex_file_elsewhere_in_the_checkout(self):
        fixture = Fixture(scratch(self), files={fx.TEX_NAME: None, "paper/notes.tex": fx.SOURCE})
        document, report = fixture.export(tex="paper/notes.tex")
        self.assertEqual(report.counts["tex"]["path"], "paper/notes.tex")
        self.assertEqual(len(document["items"]), 5)

    def test_locators_that_do_not_pin_the_tex(self):
        _, report = Fixture(scratch(self), pin=None).export()
        self.assertIn("source.tex_unpinned", report.bent)

    def test_a_link_to_a_section_that_is_not_there_is_a_problem(self):
        page = fx.page("Astray", lean="Pebbles.weigh_le", origin="cited",
                       sources=["../../sources/notes-locators.md#no-such-section"])
        _, report = Fixture(scratch(self), files={"blueprint/roadmap/heaps/astray.md": page}).export()
        self.assertEqual(len(report.problems), 1)
        self.assertIn("no-such-section", report.problems[0])

    def test_a_lean_field_that_is_no_name_is_a_problem(self):
        page = fx.page("Garbled", lean="Pebbles.weigh_le, not a name", origin="cited",
                       sources=["../../sources/notes-locators.md#weighing"])
        document, report = Fixture(scratch(self), files={"blueprint/roadmap/heaps/garbled.md": page}).export()
        self.assertIn("'not a name'", report.problems[0])
        roles = {l["role"] for l in document["links"] if l["item"] == "notes:weighing"}
        self.assertEqual(roles, {"key"})                     # the cited page's link outranks the bridge's

    def test_a_name_two_files_declare_links_nothing(self):
        second = "namespace Pebbles\ntheorem weigh_le : True := trivial\nend Pebbles\n"
        document, report = Fixture(scratch(self), files={"Pebbles/Again.lean": second}).export()
        self.assertEqual(report.counts["declarations_ambiguous"],
                         ["Pebbles.weigh_le: Pebbles/Again.lean and Pebbles/Basic.lean"])
        self.assertEqual({item["id"]: item.get("coverage") for item in document["items"]
                          if item["id"] in ("notes:weighing", "notes:conclusion")},
                         {"notes:weighing": None, "notes:conclusion": None})
        self.assertEqual([str(p) for p in check(document).problems], [])


# ---------------------------------------------------------------------------
# the command, and poppler
# ---------------------------------------------------------------------------

class TestTheCommand(unittest.TestCase):
    def cli(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cf_main.cli(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_the_parser_defaults_to_the_site_kind_the_exporter_writes(self):
        parser = argparse.ArgumentParser(prog="cf")
        subparsers = parser.add_subparsers(dest="command")
        cf_main.discover_commands(subparsers)
        for action in subparsers.choices["export"]._actions:     # noqa: SLF001 - no public API
            if isinstance(action, argparse._SubParsersAction):   # noqa: SLF001
                child = action.choices["roadmap-locators"]
                self.assertEqual(child.get_default("site_name"), roadmap_locators.SITE)
                return
        self.fail("cf export has no roadmap-locators subcommand")

    def test_it_refuses_a_pdf_that_is_not_there(self):
        fixture = Fixture(scratch(self))
        code, text = self.cli("export", "roadmap-locators", str(fixture.repo), "--commit", fixture.head,
                              "--pdf", str(fixture.root / "none.pdf"), "--pdf-url", PDF_URL,
                              "--out", str(fixture.root / "d.json"))
        self.assertEqual(code, 2, text)
        self.assertIn("--pdf", text)
        self.assertFalse((fixture.root / "d.json").exists())

    @unittest.skipUnless(POPPLER, "poppler's pdftotext and pdftoppm are not on PATH")
    def test_it_writes_the_document_and_the_report(self):
        fixture = Fixture(scratch(self))
        out, report = fixture.root / "document.json", fixture.root / "report.json"
        code, text = self.cli("export", "roadmap-locators", str(fixture.repo), "--commit", fixture.head,
                              "--pdf", str(fixture.pdf), "--pdf-url", PDF_URL, "--repo-id", REPO_ID,
                              "--source-id", "pebble-notes", "--source-title", "Pebbles",
                              "--page-offset", "0", "--out", str(out), "--report", str(report))
        self.assertEqual(code, 0, text)
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(document["items"]), 5)
        counts = json.loads(report.read_text(encoding="utf-8"))["counts"]
        self.assertEqual(counts["checkout_commit"], fixture.head)
        self.assertIn("Pebbles.gone", text)


@unittest.skipUnless(POPPLER, "poppler's pdftotext and pdftoppm are not on PATH")
class TestOnARealPdf(unittest.TestCase):
    """The same companion with nothing replaced: poppler reads the rows and renders the page."""

    def test_the_rows_poppler_reads_give_the_same_boxes_with_no_edge_on_ink(self):
        fixture = Fixture(scratch(self))
        document, report = roadmap_locators.export(
            fixture.repo, fixture.head, pdf=fixture.pdf, pdf_url=PDF_URL, repo_id=REPO_ID,
            source_id="pebble-notes", source_title="Pebbles", page_offset=0)
        self.assertEqual([str(p) for p in check(document).problems], [])
        self.assertEqual(sorted(l["id"] for l in document["locations"]),
                         ["p001-r001-002", SHARED, "p001-r005-007", "p001-r008-008"])
        counts = report.counts
        self.assertEqual(counts["spans_placed_by"], {"words": 5})
        self.assertEqual((counts["box_edges_checked"], counts["box_edges_on_ink"]), (True, []))
        self.assertNotIn("location.edges_unchecked", report.bent)

    def test_cf_overlay_export_runs_it_and_the_overlay_checks(self):
        fixture = Fixture(scratch(self))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cf_main.cli(["overlay", "init", str(fixture.repo), "--source-id", "pebble-notes",
                                "--title", "Pebbles", "-q"])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        head = fixture.commit("the overlay config")
        with redirect_stdout(out), redirect_stderr(err):
            result = overlay_export.export(
                fixture.repo, convention="roadmap-locators", commit=head,
                extra=["--pdf", str(fixture.pdf), "--pdf-url", PDF_URL, "--repo-id", REPO_ID,
                       "--source-id", "pebble-notes", "--source-title", "Pebbles", "--page-offset", "0"])
        self.assertEqual(result.sections, {"1": 8})
        record = json.loads((fixture.repo / "pdf-overlay" / "GENERATED.json")
                            .read_text(encoding="utf-8"))["annotations"]
        self.assertEqual(record["export"]["convention"], "roadmap-locators")
        self.assertEqual(record["export"]["commit"], head)
        self.assertIn(fx.TEX_NAME, [row["path"] for row in record["inputs"]])
        for argv in (["overlay", "sync", str(fixture.repo)], ["overlay", "check", str(fixture.repo)]):
            with self.subTest(command=argv[1]), redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(cf_main.cli(argv), 0, out.getvalue() + err.getvalue())


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
