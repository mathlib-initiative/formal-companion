"""`cf export informal-markers`: an informalization companion's boxes as annotations.

New code, testing scripts/annotations/export/informal_markers.py and the two
changes in scripts/overlay/ that come with it (`overlay.export`'s record of
the export's commit and the library, and `shards.section_of`), for section 10
of docs/proposals/informalize.md.

The fixture is the synthetic output repository the runtime's tests use
(tests/_informalize_doc.py): two sections, five items in version 1, six parts,
one helper declaration.  Its `markers.json` is written here rather than built,
since a build needs TeX and poppler, and the PDF is a few bytes whose sha256
the markers name -- nothing reads inside it.  The library's source is a
hundred lines with each fake declaration on the line `declarations.json` gives
it, so the snippets are cut from a real file.
"""
from __future__ import annotations

# The first import, before anything reads HOME (see tests/_hermetic.py).
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import argparse
import copy
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

import _informalize_doc as doc                                     # noqa: E402
import cf as cf_main                                               # noqa: E402
from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from cflib import vocab as cf_vocab                                # noqa: E402
from cflib.errors import CfError                                   # noqa: E402
from formal_companion_annotations import ids                       # noqa: E402
from formal_companion_annotations import validate as V             # noqa: E402
from annotations.export import boxtext, common, informal_markers   # noqa: E402
from overlay import export as overlay_export                       # noqa: E402
from overlay import shards                                         # noqa: E402

NS = shards.NAMESPACE
PDF_URL = "https://example.org/releases/pdf-v1/example-thm.pdf"
SOURCE_PATH = "ExampleLib/Topology/Glue/Basic.lean"
#: each fake declaration's keyword line, at the line tests/_informalize_doc.py gives it
SOURCE_LINES = {
    12: "structure GlueData (X : Type*) [TopologicalSpace X] where",
    13: "  left : Set X",
    30: "def glue_map {X : Type*} [TopologicalSpace X] (U V : Set X) : Glued X :=",
    31: "  Glued.mk U V",
    40: "theorem glue_theorem {X : Type*} [TopologicalSpace X] (U V : Set X)",
    41: "    (hU : IsOpen U) (hV : IsOpen V) : IsPushout U V := by",
    52: "theorem glue_surjective {X : Type*} [TopologicalSpace X] (U V : Set X)",
    53: "    (hU : IsOpen U) (hV : IsOpen V) : Surjective (glue_map U V) := by",
    60: "theorem cover_mono {X : Type*} {U V W : Set X} (h : U ⊆ V) : Covers V W := by",
    66: "theorem cover_univ {X : Type*} (U : Set X) : Covers Set.univ U := by",
    72: "theorem glue_card {X : Type*} [TopologicalSpace X] {U V : Set X} : True := by",
    80: "theorem chain_long (n : ℕ) : chain n := by",
    90: "theorem chain_short (n : ℕ) : chain (n + 1) → chain n := by",
}


# ---------------------------------------------------------------------------
# the fixture
# ---------------------------------------------------------------------------

def library_source() -> str:
    """One hundred lines of ExampleLib, each declaration on its own line, in a namespace."""
    lines = [f"-- line {n}" for n in range(1, 101)]
    lines[0] = "namespace ExampleLib"
    for number, text in SOURCE_LINES.items():
        lines[number - 1] = text
    lines[99] = "end ExampleLib"
    return "\n".join(lines) + "\n"


#: the notions `tests/_informalize_doc.py`'s conventions fix: two constants of Mathlib
#: (rows of `declarations.json`'s `references`) and one declaration of the library, as
#: ``(constant, the words of the `\fcnotion`, what the page prints inside its box)``.  The
#: last is written in mathematics, as a notation section mostly is: the page prints the
#: glued space, the source says `$\glued{U}{V}$`, and the title is the page's.
NOTIONS = [("Set", "subset", "subset"), ("IsOpen", "open", "open"),
           ("ExampleLib.GlueData", r"$\glued{U}{V}$", "U ∪ρ V")]
#: where a package's source lies under a `.lake/packages` directory
MATHLIB_PATH = "mathlib/Mathlib/Topology/Defs/Basic.lean"


def markers(source_commit: str, pdf_sha256: str, boxes: dict[str, list] | None = None) -> dict:
    """A `markers.json` for the fixture: one box per item, part and notion, two pages.

    The boxes are the ones `extract_markers.py` would write, laid out by hand;
    a part's `decls` are the `\\leanref`s the section files put in it, so part
    (b) of 1.2 names the helper as well as its key.  The notion boxes are the
    conventions', a few words wide each; `boxes` adds a box to an entry, as the
    build writes one per line of a notion the line broke.
    """
    entries: list[dict] = []
    page, y = 1, 0.1
    for item in doc.ITEMS["items"]:
        if not item["in_v1"]:
            continue
        iid, letters = item["id"], [p["letter"] for p in item["parts"]]
        own = [d["name"] for d in item["declarations"]] if not letters else []
        entries.append({"id": iid, "kind": item["kind"], "label": f"item:{iid}", "number": iid,
                        "section": item["section"], "title": item["title"],
                        "parts": [f"{iid}({c})" for c in letters], "decls": own,
                        "boxes": [[page, 0.1, round(y, 3), 0.7, 0.05]]})
        y += 0.08
        for c in letters:
            named = [d["name"] for d in item["declarations"] if d["part"] == c]
            # the drafter put the helper in the part whose text uses it
            named += [d["name"] for d in item["declarations"]
                      if d["role"] == "helper" and d["part"] is None and c == letters[-1]]
            entries.append({"id": f"{iid}({c})", "parent": iid, "part": c,
                            "label": f"item:{iid}:{c}", "number": f"{iid}({c})", "decls": named,
                            "boxes": [[page, 0.1, round(y, 3), 0.7, 0.03]]})
            y += 0.08
        if y > 0.75:
            page, y = page + 1, 0.1
    for n, (name, words, _) in enumerate(NOTIONS, start=1):
        entries.append({"id": f"notion:{name}", "notion": name, "words": words,
                        "label": f"notion:{n}", "number": f"n{n}",
                        "boxes": [[1, 0.05 + 0.22 * (n - 1), 0.9, 0.18, 0.02]]})
    for entry in entries:
        entry["boxes"] += [list(box) for box in (boxes or {}).get(entry["id"], [])]
    return {"schema": "formal-companion/informalize-markers", "schema_version": 1,
            "job": doc.JOB, "source_commit": source_commit, "pdf_sha256": pdf_sha256,
            "pages": page, "page_size_pt": [612.0, 792.0],
            "boxes_config": dict(doc.CONFIG["boxes"]),
            "box_format": "[page, x, y, w, h]; page 1-based; fractions of the page from the "
                          "top left",
            "entries": entries}


# ---------------------------------------------------------------------------
# the canonical PDF: a real one, since the export reads the text inside the boxes
# ---------------------------------------------------------------------------

#: the page, in points: the size `markers()` records
PAGE = (612.0, 792.0)
#: the characters the fixture prints outside ASCII, by the glyph name an encoding gives
#: them: poppler reads a glyph name back as the character the Adobe glyph list names
GLYPHS = {"ρ": "rho", "∪": "union"}
CODES = {char: code for code, char in enumerate(GLYPHS, start=1)}


def _escape(text: str) -> str:
    """One string for a PDF content stream, its parentheses escaped and its Greek encoded."""
    out = []
    for char in text:
        if char in CODES:
            out.append(chr(CODES[char]))
        elif char in "()\\":
            out.append("\\" + char)
        elif char.isascii() and char.isprintable():
            out.append(char)
        else:                                       # pragma: no cover - a broken fixture
            raise AssertionError(f"the fixture's font has no glyph for {char!r}")
    return "".join(out)


def minimal_pdf(pages: list[list[tuple[float, float, float, str]]]) -> bytes:
    """A PDF of `pages`, each a list of ``(x, y, size, text)`` in points from the bottom left.

    Written by hand rather than built: the suite may not need TeX, and the export's
    titles are the text poppler finds inside a box, which needs a PDF that really
    carries text.  One Helvetica font, with the glyph names of the characters the
    fixture prints outside Latin-1 in its encoding.
    """
    objects = ["<</Type/Catalog/Pages 2 0 R>>",
               "<</Type/Pages/Kids[%s]/Count %d>>" % (
                   " ".join(f"{3 + n} 0 R" for n in range(len(pages))), len(pages))]
    streams = len(pages)
    for n, page in enumerate(pages):
        objects.append(f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {PAGE[0]:g} {PAGE[1]:g}]"
                       f"/Resources<</Font<</F1 {3 + 2 * streams} 0 R>>>>"
                       f"/Contents {3 + streams + n} 0 R>>")
    for page in pages:
        content = "BT\n" + "".join(
            f"/F1 {size:g} Tf 1 0 0 1 {x:g} {y:g} Tm ({_escape(text)}) Tj\n"
            for x, y, size, text in page) + "ET\n"
        objects.append(f"<</Length {len(content)}>>stream\n{content}endstream")
    objects.append(f"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding "
                   f"{4 + 2 * streams} 0 R>>")
    objects.append("<</Type/Encoding/BaseEncoding/WinAnsiEncoding/Differences[1"
                   + "".join(f"/{name}" for name in GLYPHS.values()) + "]>>")
    out, offsets = "%PDF-1.4\n", []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj{body}endobj\n"
    start = len(out)
    out += (f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
            + "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
            + f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{start}\n%%EOF\n")
    return out.encode("latin-1")


def printed_pages(document: dict, prints: dict) -> list:
    """What the fixture's PDF prints: `prints[<entry id>]` inside that entry's boxes.

    One string goes in the entry's first box, a list one string per box.
    """
    pages: list[list[tuple[float, float, float, str]]] = [[] for _ in range(document["pages"])]
    for entry in document["entries"]:
        shown = prints.get(entry["id"])
        lines = [shown] if isinstance(shown, str) else list(shown or [])
        for (page, x, y, _, h), text in zip(entry["boxes"], lines):
            if not text:
                continue
            size = min(10.0, 0.6 * h * PAGE[1])
            pages[page - 1].append((x * PAGE[0] + 2, (1 - y - h) * PAGE[1] + 2, size, text))
    return pages


def git(root: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e.invalid",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@e.invalid",
               GIT_AUTHOR_DATE="2026-09-01T12:00:00Z", GIT_COMMITTER_DATE="2026-09-01T12:00:00Z",
               GIT_CONFIG_NOSYSTEM="1")
    done = subprocess.run(["git", "-C", str(root), *args], env=env, capture_output=True,
                          text=True)
    if done.returncode != 0:                                 # pragma: no cover - a broken fixture
        raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout.strip()


class Fixture:
    """An output repository at a commit, its canonical PDF and the library's source."""

    def __init__(self, root: Path, *, items: dict | None = None,
                 declarations: dict | None = None, prints: dict | None = None,
                 boxes: dict[str, list] | None = None):
        self.root = root
        self.boxes = boxes
        self.repo = root / "repo"
        doc.write_repo(self.repo, items=items, declarations=declarations)
        self.src = root / "library-src"
        (self.src / SOURCE_PATH).parent.mkdir(parents=True)
        (self.src / SOURCE_PATH).write_text(library_source(), encoding="utf-8")
        # a `.lake/packages` directory, with one of the two Mathlib modules in it
        self.packages = root / "packages"
        (self.packages / MATHLIB_PATH).parent.mkdir(parents=True)
        (self.packages / MATHLIB_PATH).write_text(
            "-- line 1\n-- line 2\ndef IsOpen (s : Set X) : Prop :=\n  TopologicalSpace.IsOpen s\n",
            encoding="utf-8")
        # the boxes first (they do not depend on the PDF), then the PDF that prints
        # inside them, and last the markers, which name that PDF's sha256
        layout = markers("", "", boxes)
        shown = {f"notion:{name}": text for name, _, text in NOTIONS}
        shown.update(prints or {})
        self.pdf = root / f"{doc.JOB}.pdf"
        self.pdf.write_bytes(minimal_pdf(printed_pages(layout, shown)))
        self.pdf_sha256 = common.sha256_bytes(self.pdf.read_bytes())
        git(root, "init", "-q", str(self.repo))
        self.commit("the text")
        self.write_markers(markers(self.head, self.pdf_sha256, boxes))
        self.commit("the canonical PDF's markers")

    def commit(self, message: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", message)
        self.head = git(self.repo, "rev-parse", "HEAD")
        return self.head

    def write_markers(self, document: dict) -> None:
        doc.write_json(self.repo / "informal" / "markers.json", document)

    def markers(self) -> dict:
        return json.loads((self.repo / "informal" / "markers.json").read_text(encoding="utf-8"))

    def export(self, **extra):
        options = {"pdf": self.pdf, "pdf_url": PDF_URL, "library_src": self.src,
                   "source_title": doc.SLOTS["title"], "items_approved": True}
        options.update(extra)
        return informal_markers.export(self.repo, options.pop("commit", self.head), **options)


def scratch(case: unittest.TestCase) -> Path:
    root = Path(tempfile.mkdtemp(prefix="cf-informal-markers-",
                                 dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
    case.addCleanup(shutil.rmtree, root, ignore_errors=True)
    return root


def by_id(document: dict, array: str) -> dict[str, dict]:
    return {record["id"]: record for record in document.get(array, [])}


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------

class TestTheDocument(unittest.TestCase):
    """One export of the fixture, read record by record."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="cf-informal-markers-",
                                         dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
        cls.fixture = Fixture(cls.root)
        cls.doc, cls.report = cls.fixture.export()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_it_validates_canonically_and_raises_no_warning(self):
        text = V.canonical_dumps(V.canonicalize(copy.deepcopy(self.doc)))
        checked = V.check(V.loads(text), text=text, canonical=True, lints=common.LINTS)
        self.assertEqual(checked.problems, [])
        self.assertEqual(checked.warnings, [])
        self.assertEqual(self.report.problems, [])
        self.assertEqual(self.doc["profiles"], {"site": "1.0"})

    def test_the_companion_is_the_library_at_its_own_commit(self):
        """The Lean ends are made from companion.repo at companion.commit (docs/overlay.md section 7)."""
        self.assertEqual(self.doc["companion"]["repo"], "example.org/lean-libs/ExampleLib")
        self.assertEqual(self.doc["companion"]["commit"], doc.COMMIT)
        self.assertEqual(self.doc["companion"]["name"], "ExampleLib")
        self.assertIn("informal-markers", self.doc["companion"]["generator"])
        self.assertEqual(self.doc["companion"]["generated_at"], "2026-09-01T12:00:00Z")

    def test_the_source_is_the_canonical_pdf_at_the_release_url(self):
        (source,) = self.doc["sources"]
        self.assertEqual(source["kind"], "pdf")
        self.assertEqual(source["id"], doc.JOB)
        self.assertEqual(source["title"], doc.SLOTS["title"])
        self.assertEqual(source["url"], PDF_URL)
        self.assertEqual(source["sha256"], self.fixture.pdf_sha256)
        self.assertEqual(source["bytes"], self.fixture.pdf.stat().st_size)
        self.assertEqual(source["physical_page_count"], self.fixture.markers()["pages"])
        self.assertEqual(source["page_size"], {"width_pt": 612.0, "height_pt": 792.0})
        self.assertEqual(source["printed_page_offset"], 0)

    def test_one_item_per_part_and_per_item_without_parts(self):
        items = by_id(self.doc, "items")
        self.assertEqual(sorted(i for i in items if items[i]["form"] == "numbered"), [
            "corollary-2.3", "definition-1.1", "example-2.2-a", "example-2.2-b",
            "lemma-2.1-a", "lemma-2.1-b", "theorem-1.2-a", "theorem-1.2-b"])
        self.assertNotIn("theorem-1.2", items, "an item with parts is boxed around them")
        self.assertNotIn("remark-2.4", items, "an item out of version 1 is in no PDF box")
        self.assertEqual(items["theorem-1.2-a"]["id"], ids.item_label_id("theorem", "1.2", "a"))
        self.assertEqual({k: v for k, v in items["theorem-1.2-a"].items() if k != "coverage"},
                         {"id": "theorem-1.2-a", "id_source": "label", "form": "numbered",
                          "source": doc.JOB, "kind": "Theorem", "number": "1.2", "sub": "a",
                          "section": "1", "title": "The glue theorem"})
        self.assertEqual(items["definition-1.1"]["title"], "Gluing data")
        self.assertNotIn("sub", items["definition-1.1"])
        self.assertEqual({i["coverage"] for i in items.values()}, {"formalized"})
        self.assertEqual(sum(1 for i in items.values() if i["form"] == "passage"), len(NOTIONS))

    def test_one_location_per_box_of_the_markers(self):
        boxes = {e["id"]: e["boxes"] for e in self.fixture.markers()["entries"]}
        locations = [loc for loc in self.doc["locations"] if loc["items"] == ["theorem-1.2-a"]]
        (page, x, y, w, h), = boxes["1.2(a)"]
        self.assertEqual(len(locations), 1)
        (selector,) = locations[0]["target"]["selector"]
        self.assertEqual(locations[0]["target"]["source"], doc.JOB)
        self.assertEqual(selector["type"], "PdfRegionSelector")
        self.assertEqual(selector["page"], page)
        self.assertEqual(selector["coordinate_space"]["origin"], "top_left")
        self.assertEqual(selector["coordinate_space"]["units"], "normalized")
        # the markers are measured against TeX's paper size and rendered without -cropbox
        self.assertEqual(selector["coordinate_space"]["page_box"], "MediaBox")
        self.assertIn("MediaBox", shards.DISPLAYED_BOXES)
        self.assertEqual(selector["rect"], {"x0": x, "y0": y, "x1": round(x + w, 9),
                                            "y1": round(y + h, 9)})
        self.assertEqual(len(self.doc["locations"]), sum(len(b) for b in boxes.values())
                         - len(boxes["1.2"]) - len(boxes["2.1"]) - len(boxes["2.2"]))

    def test_one_link_per_leanref_with_the_table_s_role_and_relation(self):
        links = by_id(self.doc, "links")
        self.assertEqual(sorted(links), [
            "corollary-2.3/key/ExampleLib.glue_card",
            "definition-1.1/key/ExampleLib.GlueData",
            "example-2.2-a/key/ExampleLib.chain_long",
            "example-2.2-b/key/ExampleLib.chain_short",
            "lemma-2.1-a/key/ExampleLib.cover_mono",
            "lemma-2.1-b/key/ExampleLib.cover_univ",
            "notion:ExampleLib.GlueData/key/ExampleLib.GlueData",
            "notion:IsOpen/key/IsOpen",
            "notion:Set/key/Set",
            "theorem-1.2-a/key/ExampleLib.glue_theorem",
            "theorem-1.2-b/helper/ExampleLib.glue_map",
            "theorem-1.2-b/key/ExampleLib.glue_surjective"])
        self.assertEqual(links["theorem-1.2-a/key/ExampleLib.glue_theorem"], {
            "id": "theorem-1.2-a/key/ExampleLib.glue_theorem", "item": "theorem-1.2-a",
            "declaration": "ExampleLib.glue_theorem", "role": "key", "relation": "exact",
            "provenance": informal_markers.PROVENANCE_ID})
        helper = links["theorem-1.2-b/helper/ExampleLib.glue_map"]
        self.assertEqual((helper["role"], helper["relation"]), ("helper", "related"))

    def test_a_declaration_carries_its_snippet_and_its_line_on_the_library_s_host(self):
        declarations = by_id(self.doc, "declarations")
        self.assertEqual(declarations["ExampleLib.glue_theorem"], {
            "id": "ExampleLib.glue_theorem", "id_source": "name",
            "name": "ExampleLib.glue_theorem", "kind": "theorem",
            "module": "ExampleLib.Topology.Glue.Basic", "file": SOURCE_PATH, "line": 40,
            "private": False, "anonymous": False})
        (snippet,) = [s for s in self.doc["snippets"]
                      if s["declaration"] == "ExampleLib.glue_theorem"]
        self.assertEqual(snippet["line"], 40)
        self.assertEqual(snippet["text"], SOURCE_LINES[40] + "\n" + SOURCE_LINES[41])
        (destination,) = [d for d in self.doc["destinations"]
                          if d["declaration"] == "ExampleLib.glue_theorem"]
        self.assertEqual(destination, {
            "id": "other:ExampleLib.glue_theorem", "declaration": "ExampleLib.glue_theorem",
            "site": "other", "resolution": "exact", "fragment": "L40",
            "href": f"https://example.org/lean-libs/ExampleLib/blob/{doc.COMMIT}/{SOURCE_PATH}"})
        self.assertEqual(self.report.counts["snippets_unlocated"], [])

    def test_a_notion_is_a_passage_linked_to_the_constant_it_marks(self):
        items = by_id(self.doc, "items")
        self.assertEqual(items["notion:IsOpen"],
                         {"id": "notion:IsOpen", "id_source": "declared", "form": "passage",
                          "source": doc.JOB, "kind": "passage", "title": "open",
                          "coverage": "formalized"})
        links = by_id(self.doc, "links")
        self.assertEqual(links["notion:IsOpen/key/IsOpen"],
                         {"id": "notion:IsOpen/key/IsOpen", "item": "notion:IsOpen",
                          "declaration": "IsOpen", "role": "key", "relation": "exact",
                          "provenance": informal_markers.PROVENANCE_ID})
        boxes = {e["id"]: e["boxes"] for e in self.fixture.markers()["entries"]}
        located = [loc for loc in self.doc["locations"] if loc["items"] == ["notion:IsOpen"]]
        self.assertEqual(len(located), len(boxes["notion:IsOpen"]))
        # a notion of the library itself is a declaration like any other
        self.assertEqual(by_id(self.doc, "declarations")["ExampleLib.GlueData"]["module"],
                         "ExampleLib.Topology.Glue.Basic")

    def test_a_notion_of_a_dependency_carries_its_module_and_its_documentation_page(self):
        """Without `--packages-src` nothing on disk holds Mathlib, so the card opens the
        page `declarations.json`'s `references` names and shows no statement."""
        declarations = by_id(self.doc, "declarations")
        self.assertEqual(declarations["IsOpen"], {
            "id": "IsOpen", "id_source": "name", "name": "IsOpen", "kind": "def",
            "module": "Mathlib.Topology.Defs.Basic",
            "file": "Mathlib/Topology/Defs/Basic.lean", "private": False, "anonymous": False,
            "x_package": "mathlib", "x_repo": "github.com/leanprover-community/mathlib4",
            "x_commit": doc.MATHLIB_COMMIT})
        (destination,) = [d for d in self.doc["destinations"] if d["declaration"] == "IsOpen"]
        self.assertEqual(destination, {
            "id": "doc_gen4:IsOpen", "declaration": "IsOpen", "site": "doc_gen4",
            "href": "https://leanprover-community.github.io/mathlib4_docs/"
                    "Mathlib/Topology/Defs/Basic.html",
            "resolution": "exact", "fragment": "IsOpen"})
        self.assertEqual([s for s in self.doc["snippets"] if s["declaration"] == "IsOpen"], [])
        self.assertEqual(sorted(self.report.counts["references_without_source"]),
                         ["IsOpen", "Set"])
        # the format has no field for the package, the repository or the commit: the report
        # says so, as every other `x_` key of the exporters does
        self.assertEqual(self.report.bent["declaration.x_dependency"]["count"],
                         len([d for d in declarations.values() if "x_package" in d]))

    def test_with_the_dependency_s_source_the_card_shows_the_statement_and_the_line(self):
        document, report = self.fixture.export(packages_src=self.fixture.packages)
        declarations = by_id(document, "declarations")
        self.assertEqual(declarations["IsOpen"]["line"], 3)
        (snippet,) = [s for s in document["snippets"] if s["declaration"] == "IsOpen"]
        self.assertEqual(snippet["line"], 3)
        self.assertIn("def IsOpen", snippet["text"])
        (destination,) = [d for d in document["destinations"] if d["declaration"] == "IsOpen"]
        self.assertEqual(destination["href"],
                         f"https://github.com/leanprover-community/mathlib4/blob/"
                         f"{doc.MATHLIB_COMMIT}/Mathlib/Topology/Defs/Basic.lean")
        self.assertEqual((destination["site"], destination["fragment"]), ("other", "L3"))
        # the other module is not in that directory: its card keeps the documentation page
        self.assertEqual(report.counts["references_unlocated"], ["Set"])
        self.assertEqual([d for d in document["destinations"]
                          if d["declaration"] == "Set"][0]["site"], "doc_gen4")

    def test_a_reference_row_with_neither_a_page_nor_a_commit_loses_its_destination(self):
        declarations = copy.deepcopy(doc.DECLARATIONS)
        for row in declarations["references"]:
            if row["name"] == "IsOpen":
                row.update({"doc_url": "", "repo": "", "commit": ""})
        fixture = Fixture(scratch(self), declarations=declarations)
        document, report = fixture.export()
        self.assertEqual([d for d in document["destinations"]
                          if d["declaration"] == "IsOpen"], [])
        self.assertEqual(report.lossy["destination.reference"]["count"], 1)

    def test_the_provenance_is_the_fleet_s_and_names_the_four_files_it_read(self):
        (provenance,) = self.doc["provenance"]
        self.assertEqual(provenance["actor"], "agent")
        self.assertEqual(provenance["method"], "agent_annotation")
        self.assertIs(provenance["confirmed_by_human"], True)
        self.assertEqual(provenance["tool"]["name"], "cf informalize")
        read = [row["path"] for row in self.report.counts["inputs"]]
        self.assertEqual(read, ["informal/declarations.json", "informal/informalize.json",
                                "informal/items.json", "informal/markers.json"])
        self.assertEqual(provenance["inputs"], self.report.counts["inputs"])
        for row in self.report.counts["inputs"]:
            raw = (self.fixture.repo / row["path"]).read_bytes()
            self.assertEqual(row["sha256"], common.sha256_bytes(raw))

    def test_without_the_items_approval_nobody_confirmed_the_links(self):
        document, report = self.fixture.export(items_approved=False)
        (provenance,) = document["provenance"]
        self.assertIs(provenance["confirmed_by_human"], False)
        self.assertIn("items_approval", report.counts)

    def test_the_report_says_what_the_format_cannot_carry(self):
        document, report = self.fixture.export()
        self.assertEqual(report.counts["checkout_commit"], self.fixture.head)
        self.assertEqual(report.counts["library"],
                         {"repo": "example.org/lean-libs/ExampleLib", "commit": doc.COMMIT})
        self.assertEqual(report.counts["links_by_role"],
                         {"helper": 1, "key": 8 + len(NOTIONS)})
        self.assertEqual(report.counts["items_with_parts"], 3)
        self.assertEqual(report.counts["notions"], len(NOTIONS))
        self.assertEqual(len(document["links"]), 9 + len(NOTIONS))

    def test_a_junk_value_and_a_leanref_outside_a_part_are_reported(self):
        items = copy.deepcopy(doc.ITEMS)
        for item in items["items"]:
            if item["id"] == "1.1":
                item["declarations"][0]["junk"] = ["the rank is 0 on an empty space"]
        root = scratch(self)
        fixture = Fixture(root, items=items)
        document = fixture.markers()
        for entry in document["entries"]:
            if entry["id"] == "1.2":
                entry["decls"] = ["ExampleLib.glue_map"]
        fixture.write_markers(document)
        fixture.commit("a leanref in the item's own text")
        _, report = fixture.export()
        self.assertEqual(report.lossy["link.junk"]["count"], 1)
        self.assertEqual(report.lossy["markers.leanrefs_outside_a_part"]["count"], 1)
        self.assertEqual(report.problems, [])


# ---------------------------------------------------------------------------
# the titles: what the page prints
# ---------------------------------------------------------------------------

#: the item whose title the fixture rewrites in TeX, and the head its page then prints
TITLED = "1.2"
TITLE_TEX = r"$\glued{U}{V}$"
TITLE_HEAD = "Theorem 1.2 (U ∪ρ V). Suppose that"


def no_poppler():
    """A machine with no poppler: the programs `BoxText` looks for are on no PATH."""
    return unittest.mock.patch.object(
        boxtext, "PROGRAMS", ("pdftotext-is-not-installed", "pdfinfo-is-not-installed"))


def retitled_fixture(case: unittest.TestCase, prints: dict | None = None) -> "Fixture":
    """A fixture whose item :data:`TITLED` is titled in TeX, in `items.json` and the
    markers both, with `prints` on its page."""
    items = copy.deepcopy(doc.ITEMS)
    for item in items["items"]:
        if item["id"] == TITLED:
            item["title"] = TITLE_TEX
    fixture = Fixture(scratch(case), items=items, prints=prints)
    written = fixture.markers()
    for entry in written["entries"]:
        if entry["id"] == TITLED:
            entry["title"] = TITLE_TEX
    fixture.write_markers(written)
    fixture.commit("the markers of the item titled in TeX")
    return fixture


@unittest.skipIf(shutil.which("pdftotext") is None, "poppler's pdftotext is not on PATH")
class TestTheTitlesAreTheOnesThePagePrints(unittest.TestCase):
    """A card is titled with the text of the PDF inside its boxes, never with LaTeX."""

    def test_a_notion_is_titled_with_the_text_inside_its_boxes(self):
        fixture = Fixture(scratch(self))
        document, report = fixture.export()
        items = by_id(document, "items")
        self.assertEqual(items["notion:ExampleLib.GlueData"]["title"], "U ∪ρ V")
        self.assertEqual(report.counts["titles_from_pdf"], len(NOTIONS))
        self.assertEqual(report.counts["titles_not_printed"], [])
        self.assertNotIn("title.printed_text", report.lossy)

    def test_the_boxes_of_one_notion_join_in_reading_order(self):
        """A notion the line broke has a box per line; its title is the lines, joined."""
        fixture = Fixture(scratch(self), boxes={"notion:Set": [[1, 0.05, 0.93, 0.18, 0.02]]},
                          prints={"notion:Set": ["a point", "of S"]})
        items = by_id(fixture.export()[0], "items")
        self.assertEqual(items["notion:Set"]["title"], "a point of S")

    def test_an_item_title_written_in_tex_is_the_head_the_page_prints(self):
        """`items.json`'s title is LaTeX, so the title is the parenthesis of the head;
        a part carries its item's, as the printed label repeats it."""
        fixture = retitled_fixture(self, prints={TITLED: TITLE_HEAD})
        document, report = fixture.export()
        items = by_id(document, "items")
        self.assertEqual(items["theorem-1.2-a"]["title"], "U ∪ρ V")
        self.assertEqual(items["theorem-1.2-b"]["title"], "U ∪ρ V")
        # an item whose title is plain prose is titled by the table, with nothing cropped
        self.assertEqual(items["definition-1.1"]["title"], "Gluing data")
        self.assertEqual(report.counts["titles_from_pdf"], len(NOTIONS) + 1)

    def test_an_item_head_the_page_does_not_print_falls_back_to_the_words(self):
        """Nothing is printed in that box, so there is no head to read the title from."""
        document, report = retitled_fixture(self).export()
        self.assertEqual(by_id(document, "items")["theorem-1.2-a"]["title"], "U V")
        self.assertEqual(report.counts["titles_not_printed"], [TITLED])
        self.assertIn("the item's head hold no text",
                      report.lossy["title.printed_text"]["examples"][0])

    def test_the_title_moves_and_the_ids_do_not(self):
        """The annotation id of a notion is `notion:<constant>`, whatever it is titled."""
        fixture = Fixture(scratch(self))
        with no_poppler():
            stripped, _ = fixture.export()
        printed, _ = fixture.export()
        self.assertNotEqual([i.get("title") for i in printed["items"]],
                            [i.get("title") for i in stripped["items"]])
        for array in ("items", "locations", "links", "declarations"):
            self.assertEqual([record["id"] for record in printed[array]],
                             [record["id"] for record in stripped[array]])


class TestTheTitlesWithoutPoppler(unittest.TestCase):
    """Without poppler nothing can be cropped: the words lose their markup, and say so."""

    def test_the_fallback_is_the_words_with_their_tex_markup_stripped(self):
        fixture = Fixture(scratch(self))
        with no_poppler():
            document, report = fixture.export()
        items = by_id(document, "items")
        self.assertEqual(items["notion:ExampleLib.GlueData"]["title"], "U V")
        self.assertEqual(items["notion:IsOpen"]["title"], "open")     # no markup to strip
        self.assertEqual(report.counts["titles_from_pdf"], 0)
        self.assertEqual(report.counts["titles_not_printed"],
                         [f"notion:{name}" for name, _, _ in NOTIONS])
        bucket = report.lossy["title.printed_text"]
        self.assertEqual(bucket["count"], len(NOTIONS))
        self.assertIn("pdftotext", bucket["examples"][0])
        self.assertIn("notion:Set: subset -> subset", bucket["examples"][0])
        (warning,) = [line for line in common.report_warnings(report.counts)
                      if "title" in line]
        self.assertIn(f"{len(NOTIONS)} title(s) could not be read from the PDF", warning)


class TestTheTextInsideABox(unittest.TestCase):
    """`annotations.export.boxtext`, the piece around the runtime's one cropper."""

    def test_it_reads_tex_markup_and_strips_it(self):
        self.assertTrue(boxtext.has_tex(r"$\ball{x}{\rho}$"))
        self.assertTrue(boxtext.has_tex("a point of $S$"))
        self.assertFalse(boxtext.has_tex("the composite of two homomorphisms"))
        self.assertFalse(boxtext.has_tex(None))
        self.assertEqual(boxtext.strip_tex("a point of $S$"), "a point of S")
        self.assertEqual(boxtext.strip_tex(r"$\ball{x}{\rho}$"), "x")
        # nothing is left of a title that is all markup, so it stays as it was written
        self.assertEqual(boxtext.strip_tex(r"$\R$"), r"$\R$")

    def test_it_joins_a_crop_and_ends_a_title_at_the_notion(self):
        self.assertEqual(boxtext.printed("the composite of\ntwo  homomorphisms,\n"),
                         "the composite of two homomorphisms")
        self.assertEqual(boxtext.printed("path-\nconnected space."), "path-connected space")
        self.assertEqual(boxtext.printed("  \n "), "")
        self.assertEqual(boxtext.printed("..."), "...")     # a title that is all punctuation

    def test_it_reads_the_title_out_of_an_item_head(self):
        self.assertEqual(boxtext.parenthesis_after("Theorem 1.2 (B(x, ρ)). Suppose", "Theorem 1.2"),
                         "B(x, ρ)")
        self.assertIsNone(boxtext.parenthesis_after("Theorem 1.2. Suppose", "Theorem 1.2"))
        self.assertIsNone(boxtext.parenthesis_after("Lemma 1.3 (a title)", "Theorem 1.2"))

    def test_without_poppler_it_crops_nothing_and_says_which_program(self):
        with no_poppler():
            text = boxtext.BoxText(Path("/nowhere.pdf"), 288)
            self.assertFalse(text.available)
            self.assertEqual(text.of([[1, 0.1, 0.1, 0.1, 0.1]]), "")
            self.assertIn("pdftotext", text.why)

    def test_the_cropper_is_the_runtime_s_own(self):
        """One cropper: rule 9 of the box check reads a box through the same function."""
        from informalize import runtime as informalize_runtime
        check_items = informalize_runtime.check_items()
        self.assertIs(boxtext.cropper(), check_items.crop)


# ---------------------------------------------------------------------------
# what it refuses
# ---------------------------------------------------------------------------

class TestItRefusesInputsThatDisagree(unittest.TestCase):
    """An input that disagrees with another stops the export; nothing is written."""

    def setUp(self):
        self.fixture = Fixture(scratch(self))

    def refusal(self, **extra) -> str:
        with self.assertRaises(common.ExportError) as caught:
            self.fixture.export(**extra)
        return caught.exception.message

    def test_a_pdf_the_markers_were_not_measured_on(self):
        other = self.fixture.root / "other.pdf"
        other.write_bytes(b"%PDF-1.5\n% another build\n")
        self.assertIn("markers.json was measured on", self.refusal(pdf=other))

    def test_a_checkout_that_is_not_at_the_pinned_commit(self):
        self.assertIn("not at the pinned", self.refusal(commit="0" * 40))

    def test_a_dirty_checkout(self):
        (self.fixture.repo / "informal" / "sections" / "01-gluing-spaces.tex").write_text(
            "\\begin{definition}[id=1.1]{Gluing data}\\leanref{ExampleLib.GlueData}\\end{definition}\n",
            encoding="utf-8")
        self.assertIn("uncommitted changes", self.refusal())

    def test_text_that_changed_since_the_pdf_was_built(self):
        path = self.fixture.repo / "informal" / "sections" / "02-covers.tex"
        path.write_text(path.read_text(encoding="utf-8") + "\n% a later edit\n", encoding="utf-8")
        head = self.fixture.commit("an edit after the build")
        message = self.refusal(commit=head)
        self.assertIn("informal/ changed since the PDF was built", message)
        self.assertIn("informal/sections/02-covers.tex", message)

    def test_a_second_markers_commit_is_not_a_change_of_the_text(self):
        """Only markers.json itself may differ from the commit the PDF was built from."""
        self.fixture.write_markers(dict(self.fixture.markers(), x_note="re-extracted"))
        head = self.fixture.commit("markers again")
        document, _ = self.fixture.export(commit=head)
        self.assertEqual(len(document["items"]), 8 + len(NOTIONS))

    def test_a_leanref_to_a_declaration_the_scope_does_not_hold(self):
        document = self.fixture.markers()
        for entry in document["entries"]:
            if entry["id"] == "1.1":
                entry["decls"] = ["ExampleLib.not_in_the_scope"]
        self.fixture.write_markers(document)
        head = self.fixture.commit("a leanref outside declarations.json")
        self.assertIn("is not in informal/declarations.json", self.refusal(commit=head))

    def test_a_box_that_leaves_its_page(self):
        document = self.fixture.markers()
        document["entries"][0]["boxes"] = [[1, 0.5, 0.1, 0.7, 0.05]]
        self.fixture.write_markers(document)
        head = self.fixture.commit("a box off the page")
        self.assertIn("is not a fraction of page 1", self.refusal(commit=head))

    def test_a_box_on_a_page_the_pdf_does_not_have(self):
        document = self.fixture.markers()
        document["entries"][0]["boxes"] = [[99, 0.1, 0.1, 0.7, 0.05]]
        self.fixture.write_markers(document)
        head = self.fixture.commit("a box past the last page")
        self.assertIn("is not a page of the", self.refusal(commit=head))

    def test_markers_and_items_that_describe_another_item(self):
        document = self.fixture.markers()
        document["entries"][0]["title"] = "Something else"
        self.fixture.write_markers(document)
        head = self.fixture.commit("a title the table does not give")
        self.assertIn("items.json", self.refusal(commit=head))

    def _notion(self, name: str, words: str = "a notion") -> str:
        document = self.fixture.markers()
        document["entries"].append({"id": f"notion:{name}", "notion": name, "words": words,
                                    "label": "notion:9", "number": "n9",
                                    "boxes": [[1, 0.1, 0.95, 0.2, 0.02]]})
        self.fixture.write_markers(document)
        return self.refusal(commit=self.fixture.commit(f"a notion of {name}"))

    def test_a_notion_naming_a_constant_declarations_json_does_not_hold(self):
        self.assertIn("is neither a declaration of informal/declarations.json nor a constant "
                      "of its `references`", self._notion("Mathlib.Imagined"))

    def test_a_notion_naming_a_constructor_the_format_cannot_carry(self):
        declarations = copy.deepcopy(doc.DECLARATIONS)
        declarations["references"].append({"name": "Zoo.mk", "module": "Mathlib.Zoo",
                                           "kind": "ctor", "doc_url": "", "package": "mathlib",
                                           "repo": "github.com/leanprover-community/mathlib4",
                                           "commit": doc.MATHLIB_COMMIT})
        declarations["references"].sort(key=lambda r: r["name"])
        fixture = Fixture(scratch(self), declarations=declarations)
        document = fixture.markers()
        document["entries"].append({"id": "notion:Zoo.mk", "notion": "Zoo.mk", "words": "a zoo",
                                    "label": "notion:9", "number": "n9",
                                    "boxes": [[1, 0.1, 0.95, 0.2, 0.02]]})
        fixture.write_markers(document)
        with self.assertRaises(common.ExportError) as caught:
            fixture.export(commit=fixture.commit("a notion of a constructor"))
        self.assertIn("which the annotation format does not carry", caught.exception.message)

    def test_a_notion_that_prints_nothing(self):
        self.assertIn("records no printed words", self._notion("IsOpen", words="  "))

    def test_an_items_table_drafted_against_another_library_commit(self):
        items = copy.deepcopy(doc.ITEMS)
        items["library"]["commit"] = "f" * 40
        fixture = Fixture(scratch(self), items=items)
        with self.assertRaises(common.ExportError) as caught:
            fixture.export()
        self.assertIn("drafted against library commit", caught.exception.message)

    def test_a_pdf_url_that_is_not_https(self):
        self.assertIn("is not an https URL", self.refusal(pdf_url="http://example.org/a.pdf"))

    def test_an_output_repository_with_no_markers(self):
        (self.fixture.repo / "informal" / "markers.json").unlink()
        head = self.fixture.commit("no markers")
        self.assertIn("informal/markers.json is not a file", self.refusal(commit=head))

    def test_a_box_whose_leanrefs_are_all_helpers_states_none_of_it(self):
        """Invariant I5: a record with links and no key link carries a class that excludes one."""
        document = self.fixture.markers()
        for entry in document["entries"]:
            if entry["id"] == "2.3":
                entry["decls"] = ["ExampleLib.glue_map"]
        self.fixture.write_markers(document)
        head = self.fixture.commit("a box with a helper alone")
        exported, report = self.fixture.export(commit=head)
        (item,) = [i for i in exported["items"] if i["id"] == "corollary-2.3"]
        self.assertEqual(item["coverage"], "support_only")
        self.assertEqual(report.bent["item.coverage_from_helpers"]["count"], 1)
        text = V.canonical_dumps(V.canonicalize(copy.deepcopy(exported)))
        self.assertEqual(V.check(V.loads(text), text=text, canonical=True).problems, [])

    def test_a_library_source_at_another_commit_of_the_library(self):
        """Nothing pins --library-src, so a key declaration's keyword line is checked there."""
        path = self.fixture.src / SOURCE_PATH
        path.write_text("-- a later header\n" * 3 + path.read_text(encoding="utf-8"),
                        encoding="utf-8")
        message = self.refusal()
        self.assertIn("ExampleLib.", message)
        self.assertIn("keyword outside a comment", message)

    def test_a_library_source_that_does_not_hold_the_file_at_all(self):
        """The refusal names --library-src, not the ledger this convention never reads."""
        (self.fixture.src / SOURCE_PATH).unlink()
        message = self.refusal()
        self.assertIn(f"informal/declarations.json names {SOURCE_PATH}", message)
        self.assertNotIn("ledger", message)

    def test_an_item_title_holding_a_url_is_dropped_and_reported(self):
        """The format's `external-urls` lint (L1) allows no URL in a title."""
        titled = "Gluing data (see https://example.org/notes)"
        items = copy.deepcopy(doc.ITEMS)
        for item in items["items"]:
            if item["id"] == "1.1":
                item["title"] = titled
        fixture = Fixture(scratch(self), items=items)
        written = fixture.markers()
        for entry in written["entries"]:
            if entry["id"] == "1.1":
                entry["title"] = titled
        fixture.write_markers(written)
        head = fixture.commit("the markers of the titled item")
        document, report = fixture.export(commit=head)
        (record,) = [i for i in document["items"] if i["id"] == "definition-1.1"]
        self.assertNotIn("title", record)
        self.assertEqual(report.lossy["item.title"]["count"], 1)
        text = V.canonical_dumps(V.canonicalize(copy.deepcopy(document)))
        self.assertEqual(V.check(V.loads(text), text=text, canonical=True,
                                 lints=common.LINTS).problems, [])

    def test_a_helper_cut_at_another_line_is_a_warning_the_command_prints(self):
        """A helper is exempt from the keyword check, so the bend is what is left to say so."""
        path = self.fixture.src / SOURCE_PATH
        lines = path.read_text(encoding="utf-8").split("\n")
        lines[29], lines[32] = "-- line 30", lines[29]          # glue_map, a helper, moves
        path.write_text("\n".join(lines), encoding="utf-8")
        _, report = self.fixture.export()
        self.assertEqual(report.counts["snippets_line_differs"],
                         ["ExampleLib.glue_map: 33 and 30"])
        # on a machine with no poppler every notion title falls back, which is a warning
        # of its own (TestTheTitlesWithoutPoppler)
        self.assertEqual([w for w in common.report_warnings(report.counts) if "title" not in w],
                         ["1 snippet(s) were cut at a line the document does not give for the "
                          "declaration, so the source read may not be the pinned one: "
                          "ExampleLib.glue_map: 33 and 30"])

    def test_a_key_declaration_that_no_box_names(self):
        document = self.fixture.markers()
        for entry in document["entries"]:
            if entry["id"] == "2.3":
                entry["decls"] = []
        self.fixture.write_markers(document)
        head = self.fixture.commit("a box with no leanref")
        _, report = self.fixture.export(commit=head)
        self.assertEqual(len(report.problems), 2, report.problems)
        self.assertTrue(any("names no declaration" in p for p in report.problems))
        self.assertTrue(any("ExampleLib.glue_card" in p for p in report.problems))


# ---------------------------------------------------------------------------
# the overlay carries it
# ---------------------------------------------------------------------------

class TestTheOverlayCarriesIt(unittest.TestCase):
    """`overlay.shards` converts the document, and `overlay.export` records the two commits."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="cf-informal-markers-",
                                         dir=os.environ.get("CF_TEST_TMP") or None)).resolve()
        cls.fixture = Fixture(cls.root)
        cls.doc, cls.report = cls.fixture.export()
        cls.conversion = shards.convert(cls.doc, source_id=doc.JOB)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_every_record_converts_and_passes_the_literature_links_scan(self):
        self.assertEqual(self.conversion.problems, [])
        self.assertEqual(self.conversion.counts["records"], 9 + len(NOTIONS))
        self.assertEqual(shards.scan_problems(shards.file_bytes(self.conversion)), [])

    def test_a_section_folder_per_section_of_the_text(self):
        """`shards.section_of`: an item whose section is one number goes to that folder."""
        self.assertEqual(sorted({path.split("/")[0] for path in self.conversion.files}),
                         ["1", "2", "other"])    # the conventions' notions are unnumbered
        self.assertIn("1/definition-1.1__key__ExampleLib.GlueData.json", self.conversion.files)
        self.assertEqual(sorted(p for p in self.conversion.files if p.startswith("1/theorem-1.2-b")),
                         ["1/theorem-1.2-b/helper__ExampleLib.glue_map.json",
                          "1/theorem-1.2-b/key__ExampleLib.glue_surjective.json"])

    def test_the_card_shows_the_box_the_statement_and_the_line(self):
        record = self.conversion.files["1/theorem-1.2-a__key__ExampleLib.glue_theorem.json"]
        (subject,) = record["subject"]
        (end,) = record["object"]
        self.assertEqual(subject["uri"], PDF_URL)
        self.assertEqual(subject["title"], "Theorem 1.2(a) (The glue theorem)")
        self.assertEqual(subject["location"]["page"], 1)
        self.assertEqual(record["relation"], "exactMatch")
        self.assertEqual(record["status"], "formalized")
        self.assertEqual(end["uri"], "https://example.org/lean-libs/ExampleLib")
        self.assertEqual(end["version"], doc.COMMIT)
        self.assertEqual(end["location"], {"declaration": "ExampleLib.glue_theorem",
                                           "module": "ExampleLib.Topology.Glue.Basic"})
        extension = end["extensions"][NS]
        self.assertEqual(extension["snippet"]["line"], 40)
        self.assertEqual(extension["destination"]["fragment"], "L40")
        self.assertTrue(extension["destination"]["href"].endswith(
            f"/blob/{doc.COMMIT}/{SOURCE_PATH}"))

    def test_an_item_whose_title_the_card_shows(self):
        record = self.conversion.files["1/definition-1.1__key__ExampleLib.GlueData.json"]
        self.assertEqual(record["subject"][0]["title"], "Definition 1.1 (Gluing data)")

    def test_a_part_s_card_shows_its_item_s_title_too(self):
        """A part is a part OF the item, so the printed label repeats its title."""
        record = self.conversion.files["1/theorem-1.2-a__key__ExampleLib.glue_theorem.json"]
        self.assertEqual(record["subject"][0]["title"], "Theorem 1.2(a) (The glue theorem)")

    def test_the_export_record_names_the_checkout_and_the_library(self):
        record = overlay_export._export_record(                  # noqa: SLF001 - its own test
            self.doc, {"counts": self.report.counts}, self.fixture.head[:12],
            "informal-markers", doc.JOB)
        self.assertEqual(record, {"commit": self.fixture.head, "convention": "informal-markers",
                                  "source_id": doc.JOB,
                                  "lean": {"repo": "example.org/lean-libs/ExampleLib",
                                           "commit": doc.COMMIT}})

    def test_a_report_that_names_no_commit_is_refused_rather_than_recording_the_library_s(self):
        """`overlay.export` sets `report = {}` for a report it could not read."""
        with self.assertRaises(CfError) as caught:
            overlay_export._export_record(                       # noqa: SLF001 - its own test
                self.doc, {}, self.fixture.head[:12], "informal-markers", doc.JOB)
        self.assertIn("does not name the commit it read", caught.exception.message)

    def test_a_convention_whose_companion_is_the_checkout_records_what_it_always_did(self):
        document = {"companion": {"repo": "git.example.invalid/owner/notes", "commit": "a" * 40}}
        self.assertEqual(
            overlay_export._export_record(document, {}, "a" * 12, "attribution-table", "notes"),
            {"commit": "a" * 40, "convention": "attribution-table", "source_id": "notes"})

    def test_it_is_a_convention_of_cf_overlay_export(self):
        self.assertIn("informal-markers", overlay_export.CONVENTIONS)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

class TestTheCommand(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture(scratch(self))

    def cli(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cf_main.cli(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_it_writes_the_document_and_the_report(self):
        out = self.fixture.root / "document.json"
        report = self.fixture.root / "report.json"
        code, text = self.cli("export", "informal-markers", str(self.fixture.repo),
                              "--commit", self.fixture.head, "--pdf", str(self.fixture.pdf),
                              "--pdf-url", PDF_URL, "--library-src", str(self.fixture.src),
                              "--source-title", doc.SLOTS["title"], "--items-approved",
                              "--out", str(out), "--report", str(report))
        self.assertEqual(code, 0, text)
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(document["companion"]["commit"], doc.COMMIT)
        self.assertEqual(len(document["items"]), 8 + len(NOTIONS))
        counts = json.loads(report.read_text(encoding="utf-8"))["counts"]
        self.assertEqual(counts["checkout_commit"], self.fixture.head)
        self.assertEqual(V.canonical_dumps(V.canonicalize(copy.deepcopy(document))),
                         out.read_text(encoding="utf-8"))

    def test_it_refuses_a_pdf_or_a_library_source_that_is_not_there(self):
        for flag, value in (("--pdf", self.fixture.root / "none.pdf"),
                            ("--library-src", self.fixture.root / "none")):
            with self.subTest(flag=flag):
                arguments = {"--pdf": str(self.fixture.pdf), "--library-src": str(self.fixture.src)}
                arguments[flag] = str(value)
                code, text = self.cli("export", "informal-markers", str(self.fixture.repo),
                                      "--commit", self.fixture.head, "--pdf-url", PDF_URL,
                                      "--out", str(self.fixture.root / "d.json"),
                                      *sum(([k, v] for k, v in arguments.items()), []))
                self.assertEqual(code, 2, text)
                self.assertIn(flag, text)
                self.assertFalse((self.fixture.root / "d.json").exists())

    def test_the_parser_defaults_to_the_site_kind_the_exporter_writes(self):
        parser = argparse.ArgumentParser(prog="cf")
        subparsers = parser.add_subparsers(dest="command")
        cf_main.discover_commands(subparsers)
        for action in subparsers.choices["export"]._actions:     # noqa: SLF001 - no public API
            if isinstance(action, argparse._SubParsersAction):   # noqa: SLF001
                child = action.choices["informal-markers"]
                self.assertEqual(child.get_default("site_name"), informal_markers.SITE)
                return
        self.fail("cf export has no informal-markers subcommand")


class TestCfOverlayExportRunsIt(unittest.TestCase):
    """The convention end to end, through `cf overlay export`: a real `cf export` child."""

    def test_it_writes_the_annotation_files_and_records_both_commits(self):
        fixture = Fixture(scratch(self))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cf_main.cli(["overlay", "init", str(fixture.repo), "--source-id", doc.JOB,
                                "--title", doc.SLOTS["title"], "-q"])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        head = fixture.commit("the overlay config")
        with redirect_stdout(out), redirect_stderr(err):
            result = overlay_export.export(
                fixture.repo, convention="informal-markers", commit=head,
                extra=["--pdf", str(fixture.pdf), "--pdf-url", PDF_URL,
                       "--library-src", str(fixture.src), "--source-title", doc.SLOTS["title"],
                       "--items-approved"])
        self.assertEqual(result.sections, {"1": 4, "2": 5, "other": len(NOTIONS)})
        self.assertEqual([w for w in result.warnings if "title" not in w], [])
        record = json.loads((fixture.repo / "pdf-overlay" / "GENERATED.json")
                            .read_text(encoding="utf-8"))["annotations"]
        self.assertEqual(record["export"], {
            "commit": head, "convention": "informal-markers", "source_id": doc.JOB,
            "lean": {"repo": "example.org/lean-libs/ExampleLib", "commit": doc.COMMIT}})
        self.assertEqual([row["path"] for row in record["inputs"]],
                         ["informal/declarations.json", "informal/informalize.json",
                          "informal/items.json", "informal/markers.json"])
        self.assertEqual(record["count"], 9 + len(NOTIONS))
        written = sorted(p.relative_to(fixture.repo).as_posix()
                         for p in (fixture.repo / "annotations").rglob("*.json"))
        self.assertIn("annotations/1/definition-1.1__key__ExampleLib.GlueData.json", written)
        self.assertIn("annotations/_defaults.json", written)
        # and the rest of the overlay is reused as it stands
        for argv in (["overlay", "sync", str(fixture.repo)],
                     ["overlay", "check", str(fixture.repo)]):
            with self.subTest(command=argv[1]), redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(cf_main.cli(argv), 0, out.getvalue() + err.getvalue())


class TestTheRelationsAreTheOnesThatConvertBack(unittest.TestCase):
    def test_every_relation_of_the_item_table_is_written_as_the_token_shards_reads(self):
        self.assertEqual(sorted(informal_markers.RELATION_TOKENS),
                         sorted(cf_vocab.INFORMALIZE_RELATIONS))
        for relation, token in informal_markers.RELATION_TOKENS.items():
            with self.subTest(relation=relation):
                self.assertEqual(shards.RELATIONS[token], relation)


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
