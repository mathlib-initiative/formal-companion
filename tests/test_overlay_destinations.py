"""The generic destinations producer: a synthetic literate site, no companion.

Tests ``scripts/overlay/destinations.py`` against section 7 of the overlay
contract (`docs/overlay.md`, seeded as the overlay template's SPEC.md).  The
site is
three pages built in a temporary directory, and every declaration is synthetic
(``Notes.Box.volume_nonneg`` and friends): no companion's Lean, no companion's
module names (CONTRIBUTING.md invariant 4).  Each case the producer has to
tell apart has a page of its own: an anchor that is there, one that is not, one
that is there twice, a declaration with no name at all, and a module the build
rendered no page for.
"""
from __future__ import annotations

# The first import, before anything reads HOME (see tests/_hermetic.py).
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import io
import json
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

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V              # noqa: E402
from formal_companion_annotations import vocab                      # noqa: E402
from cflib.errors import CfError                                    # noqa: E402
from overlay import destinations as D                               # noqa: E402

BOX_PAGE = """<!doctype html>
<html><body>
<h1 id="Notes___Box">Notes.Box</h1>
<section><a id="Notes___Box___volume_nonneg"></a><code>volume_nonneg</code></section>
<section><a id="Notes___Box___volume_nonneg_strict"></a><code>strict</code></section>
<section><a id="Notes___Box___twice"></a><a id="Notes___Box___twice"></a></section>
</body></html>
"""
BASIC_PAGE = """<!doctype html>
<html><body>
<h1 id="Notes___Basic">Notes.Basic</h1>
<section><span data-binding="kw-occ-example-412"></span></section>
</body></html>
"""
INDEX_PAGE = """<!doctype html>
<html><body><h1>Elementary Sets: Lecture Notes (synthetic)</h1></body></html>
"""


def declaration(did, module, *, name=None, kind="theorem", id_source="name",
                anonymous=False) -> dict:
    record = {"id": did, "id_source": id_source, "kind": kind, "module": module,
              "file": module.replace(".", "/") + ".lean", "private": False,
              "anonymous": anonymous}
    if name is not None:
        record["name"] = name
    return record


DECLARATIONS = [
    declaration("Notes.Box.volume_nonneg", "Notes.Box", name="Notes.Box.volume_nonneg"),
    declaration("Notes.Box.volume_pos", "Notes.Box", name="Notes.Box.volume_pos"),
    declaration("Notes.Box.twice", "Notes.Box", name="Notes.Box.twice"),
    dict(declaration("example:0123456789ab", "Notes.Basic", kind="example",
                     id_source="statement_hash", anonymous=True),
         statement_sha256="0123456789ab" + "0" * 52),
    declaration("Notes.Missing.lemma_a", "Notes.Missing", name="Notes.Missing.lemma_a"),
]


def write_site(root: Path) -> Path:
    """The synthetic literate site: an index and two module pages."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(INDEX_PAGE, encoding="utf-8")
    for module, page in (("Notes/Box", BOX_PAGE), ("Notes/Basic", BASIC_PAGE)):
        target = root / module
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(page, encoding="utf-8")
    return root


def document(declarations=None) -> dict:
    """The smallest document that carries these declarations."""
    return {
        "schema": vocab.ANNOTATION_SCHEMA,
        "schema_version": vocab.ANNOTATION_SCHEMA_VERSION,
        "companion": {"name": "notes-companion", "repo": "example.invalid/org/notes-companion",
                      "commit": "0" * 40, "generated_at": "2026-01-01T00:00:00Z",
                      "generator": "overlay destinations (test)"},
        "sources": [{"id": "notes-lean", "kind": "lean", "role": "reference",
                     "title": "The companion's Lean sources (synthetic)",
                     "sha256": "cd" * 32}],
        "items": [], "links": [], "provenance": [],
        "declarations": list(DECLARATIONS if declarations is None else declarations),
    }


def by_declaration(records) -> dict:
    return {r["declaration"]: r for r in records}


class TestThePageAndTheAnchor(unittest.TestCase):

    def test_a_module_maps_to_its_literate_page(self):
        self.assertEqual(D.module_page("Notes.Box"), "Notes/Box/index.html")
        self.assertEqual(D.module_page("Notes"), "Notes/index.html")

    def test_a_module_with_no_page_to_derive_is_refused(self):
        for module in ("", "   ", "/Notes", "Notes:2", "Notes Box"):
            with self.subTest(module=module):
                with self.assertRaises(CfError):
                    D.module_page(module)

    def test_a_dotted_module_cannot_name_a_page_outside_the_site(self):
        # the dots become separators before anything else, so a module cannot
        # spell a parent segment: there is no page outside the site root to read
        page = D.module_page("Notes/../../etc")
        self.assertNotIn("..", page)
        self.assertTrue(D.SITE_PATH.match(page), page)

    def test_an_anchor_id_is_the_name_with_each_dot_replaced(self):
        self.assertEqual(D.anchor_id("Notes.Box.volume_nonneg"),
                         "Notes___Box___volume_nonneg")
        self.assertEqual(D.anchor_id("volume_nonneg"), "volume_nonneg")
        with self.assertRaises(CfError):
            D.anchor_id("Notes.Box volume_nonneg")

    def test_an_anchor_is_counted_as_an_id_and_never_as_a_substring(self):
        self.assertEqual(D.anchor_count(BOX_PAGE, "Notes___Box___volume_nonneg"), 1)
        self.assertEqual(D.anchor_count(BOX_PAGE, "Notes___Box___twice"), 2)
        self.assertEqual(D.anchor_count(BOX_PAGE, "Notes___Box___absent"), 0)
        self.assertEqual(D.anchor_count("<a id='Notes___Box'></a>", "Notes___Box"), 1)
        self.assertEqual(D.anchor_count("<a href='#Notes___Box'></a>", "Notes___Box"), 0)


class TestTheDestinations(unittest.TestCase):

    def build(self, **kwargs) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            doc = kwargs.pop("doc", None) or document()
            return D.build_destinations(site, doc, **kwargs)

    def test_an_anchor_the_page_carries_is_an_exact_destination(self):
        record = by_declaration(self.build()["destinations"])["Notes.Box.volume_nonneg"]
        self.assertEqual(record, {"id": "verso_literate:Notes.Box.volume_nonneg",
                                  "declaration": "Notes.Box.volume_nonneg",
                                  "site": "verso_literate", "href": "Notes/Box/index.html",
                                  "fragment": "Notes___Box___volume_nonneg",
                                  "resolution": "exact"})

    def test_an_anchor_the_page_lacks_falls_back_to_the_module_page(self):
        record = by_declaration(self.build()["destinations"])["Notes.Box.volume_pos"]
        self.assertEqual(record["resolution"], "fallback")
        self.assertEqual(record["href"], "Notes/Box/index.html")
        self.assertNotIn("fragment", record)
        self.assertEqual(record["fallback_label"], D.FALLBACK_LABELS["absent"])
        self.assertIn("not on the page", record["fallback_label"])

    def test_an_anchor_the_page_carries_twice_is_not_called_exact(self):
        record = by_declaration(self.build()["destinations"])["Notes.Box.twice"]
        self.assertEqual(record["resolution"], "fallback")
        self.assertEqual(record["fallback_label"], D.FALLBACK_LABELS["ambiguous"])

    def test_a_declaration_with_no_name_falls_back_to_its_module_page(self):
        record = by_declaration(self.build()["destinations"])["example:0123456789ab"]
        self.assertEqual(record["resolution"], "fallback")
        self.assertEqual(record["href"], "Notes/Basic/index.html")
        self.assertIn("anonymous", record["fallback_label"])

    def test_a_module_the_build_rendered_no_page_for_falls_back_to_the_index(self):
        record = by_declaration(self.build()["destinations"])["Notes.Missing.lemma_a"]
        self.assertEqual(record["resolution"], "fallback")
        self.assertEqual(record["href"], "index.html")
        self.assertEqual(record["fallback_label"], D.FALLBACK_LABELS["no_page"])
        self.assertIn("no page in this build", record["fallback_label"])

    def test_every_declaration_gets_exactly_one_destination_sorted_by_id(self):
        records = self.build()["destinations"]
        self.assertEqual(len(records), len(DECLARATIONS))
        ids = [r["id"] for r in records]
        self.assertEqual(ids, sorted(ids, key=V.order_key))
        self.assertEqual(sorted(by_declaration(records)),
                         sorted(d["id"] for d in DECLARATIONS))
        for record in records:
            with self.subTest(destination=record["id"]):
                self.assertTrue(D.SITE_PATH.match(record["href"]), record)
                if "fragment" in record:
                    self.assertTrue(D.FRAGMENT.match(record["fragment"]), record)
                self.assertEqual(record["id"], f"verso_literate:{record['declaration']}")

    def test_the_site_kind_and_the_index_page_are_arguments(self):
        records = self.build(site="doc_gen4")["destinations"]
        self.assertEqual({r["site"] for r in records}, {"doc_gen4"})
        self.assertTrue(all(r["id"].startswith("doc_gen4:") for r in records))
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            (site / "start.html").write_text(INDEX_PAGE, encoding="utf-8")
            moved = D.build_destinations(site, document(), index="start.html")
        record = by_declaration(moved["destinations"])["Notes.Missing.lemma_a"]
        self.assertEqual(record["href"], "start.html")

    def test_the_producer_refuses_what_it_cannot_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            with self.assertRaises(CfError):            # not a site kind
                D.build_destinations(site, document(), site="literate")
            with self.assertRaises(CfError):            # an index that is not there
                D.build_destinations(site, document(), index="missing.html")
            with self.assertRaises(CfError):            # an index outside the site
                D.build_destinations(site, document(), index="../index.html")
            with self.assertRaises(CfError):            # a declaration with no id
                D.build_destinations(site, dict(document(), declarations=[{"module": "Notes"}]))
            with self.assertRaises(CfError):            # not a site at all
                D.build_destinations(site / "nowhere", document())

    def test_the_fragment_validates_inside_a_document_of_those_declarations(self):
        with tempfile.TemporaryDirectory() as tmp:
            fragment = D.build_destinations(write_site(Path(tmp)), document())
        doc = V.canonicalize(dict(document(), destinations=fragment["destinations"]))
        text = V.canonical_dumps(doc)
        report = V.check(doc, text=text, canonical=True)
        self.assertEqual(report.problems, [])
        self.assertEqual(report.warnings, [])


class TestTheCommand(unittest.TestCase):

    def run_main(self, argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = D.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_main_writes_a_canonical_fragment_and_counts_the_resolutions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = write_site(root / "site")
            doc = root / "annotations.json"
            doc.write_text(V.canonical_dumps(V.canonicalize(document())), encoding="utf-8")
            target = root / "fragment.json"
            code, out, err = self.run_main(["--site", str(site), "--doc", str(doc),
                                            "--out", str(target)])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("5 destinations: 1 exact, 4 fallback", out)
            text = target.read_text(encoding="utf-8")
        fragment = json.loads(text)
        self.assertEqual(text, V.canonical_dumps(V.canonicalize(fragment)))
        self.assertEqual(len(fragment["destinations"]), 5)

    def test_main_refuses_a_document_that_is_not_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site = write_site(root / "site")
            doc = root / "annotations.json"
            doc.write_text('{"schema": "something/else", "schema_version": "1.0"}\n',
                           encoding="utf-8")
            code, _out, err = self.run_main(["--site", str(site), "--doc", str(doc),
                                             "--out", str(root / "fragment.json")])
            self.assertEqual(code, 2)
            self.assertIn(vocab.ANNOTATION_SCHEMA, err)
            doc.write_text("not json at all\n", encoding="utf-8")
            code, _out, err = self.run_main(["--site", str(site), "--doc", str(doc),
                                             "--out", str(root / "fragment.json")])
            self.assertEqual(code, 2)
            self.assertIn("not a readable annotation document", err)
            code, _out, err = self.run_main(["--site", str(site), "--doc", str(root / "gone.json"),
                                             "--out", str(root / "fragment.json")])
            self.assertEqual(code, 2)
            self.assertIn("--doc", err)
            self.assertFalse((root / "fragment.json").exists())

    def test_main_refuses_a_site_that_was_never_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "annotations.json"
            doc.write_text(V.canonical_dumps(V.canonicalize(document())), encoding="utf-8")
            code, _out, err = self.run_main(["--site", str(root / "site"), "--doc", str(doc),
                                             "--out", str(root / "fragment.json")])
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
