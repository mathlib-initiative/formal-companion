"""`templates/overlay/runtime/tools/verso_extract.py`: the statements and hovers `build_site.py` publishes.

`docs/overlay.md` sections 4 and 5. Every page here is synthetic HTML in the
shape a Verso literate build writes (a `div.code-box` of `code.hl.lean.block`
elements whose tokens are `span.token`s with `data-verso-hover` and
`data-binding`, wrapped in `a` for a link, the site root as the page's
`<base>`), about an invented `Notes` companion: no page of any real site.
"""
from __future__ import annotations

# Pin HOME before anything else: `unittest discover` loads no conftest.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import contextlib
import html
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
TOOLS = SKILL / "templates" / "overlay" / "runtime" / "tools"
TARGETS = SKILL / "templates" / "overlay" / "tests" / "card-targets.json"


def load_tool(name: str):
    """Import a runtime tool by path, the way a companion runs it."""
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"overlay_{name}", path)
    if spec is None or spec.loader is None:                  # pragma: no cover
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VERSO = load_tool("verso_extract")
NAMESPACE = VERSO.overlay_annotations.NAMESPACE


# ---------------------------------------------------------------------------
# a synthetic Verso site
# ---------------------------------------------------------------------------

def tok(kind: str, text: str, hover: int | None = None, binding: str = "", ident: str | None = None) -> str:
    attrs = f' class="{kind} token" data-binding="{html.escape(binding)}"'
    if hover is not None:
        attrs += f' data-verso-hover="{hover}"'
    if ident:
        attrs += f' id="{html.escape(ident)}"'
    return f"<span{attrs}>{html.escape(text)}</span>"


def gap(text: str = " ") -> str:
    return f'<span class="inter-text">{html.escape(text)}</span>'


def link(href: str, inner: str) -> str:
    return f'<a href="{html.escape(href)}" title="Definition">{inner}</a>'


def block(*parts: str) -> str:
    return '<code class="hl lean block" data-lean-context="lean">' + "".join(parts) + "</code>"


def box(*blocks: str) -> str:
    return '<div class="code-box">\n  ' + "\n  ".join(blocks) + "</div>\n"


def prose(text: str) -> str:
    return f'<div class="verso-text mod-doc"><p>{html.escape(text)}</p></div>\n'


def page(*parts: str, base: str = "../../") -> str:
    return (f'<!DOCTYPE html>\n<html><head><base href="{base}"><title>Notes.Box</title></head><body>\n'
            + "".join(parts) + "</body></html>\n")


def goals(text: str) -> str:
    """A tactic with its goal view: the view is not code and must never reach a statement."""
    return ('<span class="tactic"><label for="ts-1">' + tok("keyword", "by", 40) + "</label>"
            '<input type="checkbox" class="tactic-toggle" id="ts-1">'
            '<span class="tactic-state"><span class="goal"><span class="hypotheses">'
            '<span class="hypothesis"><span class="name">' + tok("var", "h", 41, "var-h")
            + '</span></span></span><span class="conclusion">'
            + f'<span class="prefix">⊢ </span><span class="type">{html.escape(text)}</span></span></span></span></span>')


BOX_PAGE = "Notes/Box/index.html"
#: A one-element box (literal newlines) holding two declarations, the second with its body.
ONE_ELEMENT = box(block(
    tok("keyword", "theorem"), gap(), tok("const", "side_pos", 10, "const-Notes.Box.side_pos",
                                          ident="Notes___Box___side_pos"),
    gap(), tok("punctuation bracket", "(", None, "kw-occ-1"), tok("var", "b", 11, "var-b1"), gap(),
    tok("built-in delim", ":", None, "kw-occ-1"), gap(), link("Notes/Box/#Notes___Box", tok("const", "Box", 12)),
    tok("punctuation bracket", ")", None, "kw-occ-1"), gap(), tok("built-in delim", ":"), gap(),
    tok("literal number", "0", 13), gap(), tok("unknown", "<"), gap(), tok("var", "b", 11, "var-b1"),
    tok("built-in delim", "."), link("Notes/Box/#Notes___Box___side", tok("const", "side", 14)), gap(),
    tok("built-in delim", ":="), gap(), tok("const", "b", 11), gap("\n\n"),
    tok("keyword", "noncomputable"), gap(), tok("keyword", "def"), gap(),
    tok("const", "volume", 20, "const-Notes.Box.volume", ident="Notes___Box___volume"), gap(),
    tok("punctuation bracket", "(", None, "kw-occ-2"), tok("var", "b", 21, "var-b2"), gap(),
    tok("built-in delim", ":", None, "kw-occ-2"), gap(), link("Notes/Box/#Notes___Box", tok("const", "Box", 12)),
    tok("punctuation bracket", ")", None, "kw-occ-2"), gap(), tok("built-in delim", ":"), gap(),
    link("Notes/Real/#Notes___Real", tok("const", "ℝ", 22)), gap(), tok("built-in delim", ":="), gap("\n  "),
    tok("var", "b", 21, "var-b2"), tok("built-in delim", "."),
    link("Notes/Box/#Missing", tok("const", "side", 14)), gap(), tok("unknown", "*"), gap(),
    tok("var", "b", 21, "var-b2"), tok("built-in delim", "."),
    link("https://elsewhere.invalid/Box.html#side", tok("const", "side", 14)), gap(), tok("unknown", "+"), gap(),
    link("Elsewhere/#x", tok("const", "zero", 23)),
))
#: The literate page keeps the code and moves each docstring and comment into prose.
PER_LINE = box(
    block(tok("keyword", "structure"), gap(), tok("const", "Box", 12, ident="Notes___Box"), gap(),
          tok("keyword", "where")),
    block(gap("  "), tok("const", "side", 14, ident="Notes___Box___side"), gap(), tok("built-in delim", ":"), gap(),
          link("Notes/Real/#Notes___Real", tok("const", "ℝ", 22))),
    block(gap("  "), tok("const", "pos", 15), gap(), tok("built-in delim", ":"), gap(), tok("literal number", "0"),
          gap(), tok("unknown", "<"), gap(), tok("var", "side", 16)),
)
ATTRIBUTED = box(block(
    tok("unknown", "@["), tok("const", "simp", 30), tok("unknown", "]"), gap("\n"),
    tok("keyword", "theorem"), gap(), tok("const", "volume_def", 31, ident="Notes___Box___volume_def"), gap(),
    tok("built-in delim", ":"), gap(), tok("const", "True", 32), gap(), tok("built-in delim", ":="), gap(),
    tok("const", "trivial", 33),
))
TACTIC = box(block(
    tok("keyword", "def"), gap(), tok("const", "two", 50, ident="Notes___Box___two"), gap(),
    tok("built-in delim", ":"), gap(), tok("const", "ℕ", 51), gap(), tok("built-in delim", ":="), gap(),
    goals("ℕ"), gap("\n  "), tok("keyword", "exact"), gap(), tok("literal string", '"a b"', 52),
))
REAL_PAGE = page(box(block(tok("keyword", "def"), gap(), tok("const", "Real", 22, ident="Notes___Real"))))
#: Two code elements, neither starting with whitespace: a line break must still separate them.
TWO_BLOCKS = box(
    block(tok("keyword", "def"), gap(), tok("const", "one", 60, ident="Notes___Box___one"), gap(),
          tok("built-in delim", ":="), gap(), tok("literal number", "1")),
    block(tok("keyword", "def"), gap(), tok("const", "uno", 61, ident="Notes___Box___uno"), gap(),
          tok("built-in delim", ":="), gap(), tok("literal number", "1")),
)
#: A message the page shows in its code (`.hover-info`) and a "…" the code itself holds.
MESSAGES = box(block(
    tok("keyword", "def"), gap(), tok("const", "three", 62, ident="Notes___Box___three"),
    '<span class="hover-info"><code class="hl lean">unused variable</code></span>', gap(),
    tok("built-in delim", ":="), gap(), tok("literal number", "3"), gap("\n\n"),
    tok("keyword", "def"), gap(), tok("const", "dots", 63, ident="Notes___Box___dots"), gap(),
    tok("built-in delim", ":="), gap(), tok("literal string", '"…"'),
))
#: A page that gives an id to a use of a name that is not its declaration, as Verso does to `Set`.
GEO_PAGE = page(box(block(
    tok("keyword", "def"), gap(), tok("const", "carrier", 70, "const-Notes.Geo.carrier", ident="Notes___Geo___carrier"),
    gap(), tok("built-in delim", ":"), gap(), tok("const", "Set", 71, "const-Set", ident="Set"),
)))
USES_GEO = box(block(
    tok("keyword", "def"), gap(), tok("const", "sets", 72, ident="Notes___Box___sets"), gap(), tok("built-in delim", ":"),
    gap(), link("Notes/Geo/#Set", tok("const", "Set", 71)), gap(),
    link("Notes/Geo/#Notes___Geo___carrier", tok("const", "carrier", 70)),
))
#: A tagged example: no anchor, its tag comment in its box (and named once in the prose before it).
EXAMPLE_TAG = "example:notes-box-pos"
EXAMPLE = box(block(
    tok("comment delimiter", "/-"), tok("comment block", f" `{EXAMPLE_TAG}` "), tok("comment delimiter", "-/"),
    gap("\n"), tok("keyword", "open"), gap(), tok("const", "Notes", 80), gap(), tok("keyword", "in"), gap("\n"),
    tok("keyword", "example"), gap(), tok("built-in delim", ":"), gap(), tok("literal number", "0"), gap(),
    tok("unknown", "<"), gap(), link("Notes/Box/#Notes___Box", tok("const", "Box", 12)), gap(),
    tok("built-in delim", ":="), gap(), tok("const", "trivial", 33),
))
#: Verso often puts a tag comment inside the last tactic of the declaration before it.
NESTED_TAG = "example:notes-nested"
NESTED_EXAMPLE = box(block(
    tok("keyword", "theorem"), gap(), tok("const", "prev", 90, ident="Notes___Box___prev"), gap(),
    tok("built-in delim", ":"), gap(), tok("const", "True", 32), gap(), tok("built-in delim", ":="), gap(),
    tok("keyword", "by"), gap("\n  "),
    '<span class="tactic"><label for="ts-9">' + tok("keyword", "trivial", 91) + gap("\n\n")
    + tok("comment delimiter", "/-") + tok("comment block", f" `{NESTED_TAG}` ") + tok("comment delimiter", "-/")
    + '</label><input type="checkbox" class="tactic-toggle" id="ts-9"><span class="tactic-state">'
    + '<span class="goal">All goals completed!</span></span></span>',
    gap("\n"), tok("keyword", "example"), gap(), tok("built-in delim", ":"), gap(), tok("const", "True", 32), gap(),
    tok("built-in delim", ":="), gap(), tok("const", "trivial", 33),
))
DOCS = {
    "10": "<code>Notes.Box.side_pos (b : Box) : 0 &lt; b.side</code><span class=\"sep\"></span>"
          "<code class=\"docstring\">The side is positive.</code>",
    "11": "<code>Box</code>",
    "12": "<code>Notes.Box : Type</code><span class=\"sep\"></span><code class=\"docstring\">A box.\n\nIts side is "
          "`side`.</code>",
    "13": "<code>ℕ</code>",
    "14": "<code>Notes.Box.side (self : Box) : ℝ</code>",
    "20": "<code>Notes.Box.volume (b : Box) : ℝ</code>",
    "21": "<code>Box</code>",
    "22": "<code>Notes.Real : Type</code>",
    "30": "<code class=\"docstring\">The simplifier's attribute.</code>",
    "40": "<code class=\"docstring\">`by tac` constructs a term of the expected type by running the tactic(s) `tac`.</code>",
    "41": "<code>False</code>",
    "52": "<code><span class=\"literal string\">\"a b\"</span> : String</code>",
    "70": "<code>Notes.Geo.carrier : Type</code>",
    "71": "<code>Set.{u} (α : Type u) : Type u</code>",
    "99": "<code>never used</code>",
}


def lean_end(declaration: str, snippet: str, *, module: str = "Notes.Box", **destination) -> dict:
    destination.setdefault("resolution", "exact")
    return {"type": "lean", "uri": "https://git.example.invalid/notes", "role": "key",
            "location": {"declaration": declaration, "module": module},
            "extensions": {NAMESPACE: {"destination": destination, "snippet": {"line": 1, "text": snippet}}}}


def example_end(tag: str, snippet: str) -> dict:
    """An example's end: no declaration name, its module page as a fallback, its tag as its id."""
    return {"type": "lean", "uri": "https://git.example.invalid/notes", "role": "key",
            "location": {"lines": [7], "module": "Notes.Box"},
            "extensions": {NAMESPACE: {"declaration": {"anonymous": True, "id": tag, "kind": "example"},
                                       "destination": {"resolution": "fallback", "fallback_label": "module page"},
                                       "snippet": {"line": 7, "text": snippet}}}}


class AnySite:
    """A site where every page and anchor exists: what is left out is what `link` itself refuses."""

    def base(self, _page: str) -> str:
        return ""

    def is_directory(self, _relative: str) -> bool:
        return False

    def declares(self, _relative: str, _fragment: str) -> bool:
        return True


def entries(*ends: dict) -> list[tuple[str, dict]]:
    return [(f"annotations/1.1/{n:02d}.json", {"id": f"item:{n}", "subject": [], "object": [end]})
            for n, end in enumerate(ends)]


def joined(runs: list[list]) -> str:
    return "".join(run[1] for run in runs)


class VersoCase(unittest.TestCase):
    #: Where the pages are, under the temporary directory.
    ROOT: tuple[str, ...] = ("site",)

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="overlay-verso-test-")
        self.addCleanup(temporary.cleanup)
        self.site = Path(temporary.name).joinpath(*self.ROOT)
        self.write(BOX_PAGE, page(prose("Boxes."), PER_LINE, ONE_ELEMENT, prose("Attributes."), ATTRIBUTED,
                                  TACTIC, TWO_BLOCKS, MESSAGES, USES_GEO, prose(f"See `{EXAMPLE_TAG}` below."),
                                  EXAMPLE, NESTED_EXAMPLE))
        self.write("Notes/Real/index.html", REAL_PAGE)
        self.write("Notes/Geo/index.html", GEO_PAGE)
        self.write(VERSO.DOCS_NAME, json.dumps(DOCS))

    def write(self, relative: str, text: str) -> Path:
        path = self.site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def extract(self, *ends: dict) -> tuple[dict, dict]:
        return VERSO.extract(self.site, entries(*ends))

    def statement(self, declaration: str, snippet: str, **destination) -> list[list] | None:
        value, _counts = self.extract(lean_end(declaration, snippet, **destination))
        found = value["statements"].get(f"{BOX_PAGE}#{declaration.replace('.', '___')}")
        return found[0] if found else None


class TestTheStatement(VersoCase):
    def test_a_field_excerpt_keeps_its_context_label_and_hovers_only_the_actual_field(self):
        snippet = "structure Box where\n  …\n  side : ℝ"
        runs = self.statement("Notes.Box.side", snippet)
        self.assertIsNotNone(runs)
        self.assertEqual(joined(runs), snippet)
        self.assertIn(["el", "…"], runs)
        self.assertTrue(any(r[0] == "const" and r[1] == "side" and r[2] == 14 for r in runs))
        self.assertIsNone(self.statement("Notes.Box.side", snippet.replace("Box", "OtherBox")))
        self.assertIsNone(self.statement("Notes.Box", snippet), "the field must cover its own anchor")

    def test_a_one_element_box_gives_the_snippet_with_its_tokens_body_included(self):
        snippet = ("noncomputable def volume (b : Box) : ℝ :=\n  b.side * b.side + zero")
        runs = self.statement("Notes.Box.volume", snippet)
        self.assertEqual(joined(runs), snippet, "the statement is the snippet's text, exactly")
        self.assertEqual(runs[:4], [["kw", "noncomputable"], ["tx", " "], ["kw", "def"], ["tx", " "]])
        self.assertEqual(runs[4], ["const", "volume", 20])
        self.assertEqual(runs[5:7], [["tx", " "], ["tx", "(", None, 1]])
        self.assertEqual(runs[7], ["var", "b", 21, 2], "the three uses of b share a binding")
        self.assertIn(["const", "Box", 12, None, "Notes/Box/index.html#Notes___Box"], runs)
        self.assertIn(["const", "ℝ", 22, None, "Notes/Real/index.html#Notes___Real"], runs)
        self.assertEqual([run for run in runs if run[1] == "side"], [["const", "side", 14], ["const", "side", 14]],
                         "a link to a missing anchor and a link off the site are dropped")
        self.assertIn(["const", "zero"], runs, "a link to a missing page is dropped, and so is an unknown hover")

    def test_the_line_above_the_name_starts_the_statement_and_a_comment_the_page_moved_stays(self):
        snippet = "-- The definition by `simp`.\n@[simp]\ntheorem volume_def : True := trivial"
        runs = self.statement("Notes.Box.volume_def", snippet)
        self.assertEqual(joined(runs), snippet)
        self.assertEqual(runs[0], ["cm", "-- The definition by `simp`."])
        self.assertEqual(runs[1:4], [["tx", "\n@["], ["const", "simp", 30], ["tx", "]\n"]])

    def test_per_line_code_elements_and_docstrings_the_page_moved_into_prose(self):
        snippet = ("structure Box where\n  /-- The side. -/\n  side : ℝ\n  /-- It is positive.\n  -/\n"
                   "  pos : 0 < side")
        runs = self.statement("Notes.Box", snippet)
        self.assertEqual(joined(runs), snippet)
        self.assertEqual([run[1] for run in runs if run[0] == "cm"], ["/-- The side. -/", "/-- It is positive.\n  -/"])
        self.assertIn(["var", "side"], runs, "a hover the table does not hold is dropped")
        self.assertIn(["lit", "0"], runs)

    def test_a_trailing_ellipsis_is_kept_as_the_cut_mark(self):
        snippet = "noncomputable def volume (b : Box) : ℝ :=\n  b.side\n…"
        runs = self.statement("Notes.Box.volume", snippet)
        self.assertEqual(joined(runs), snippet)
        self.assertEqual(runs[-2:], [["tx", "\n"], ["el", "…"]])

    def test_an_internal_ellipsis_a_mismatch_or_another_declaration_is_no_statement(self):
        for snippet in ("structure Box where\n  …\n  pos : 0 < side",
                        "noncomputable def volume (b : Box) : ℕ :=\n  b.side",
                        "noncomputable def volume (b : Box) : ℝ := b.sides",
                        "noncomputabledef volume (b : Box) : ℝ :=\n  b.side",
                        "noncomputable def volume(b : Box) : ℝ :=\n  b.side",
                        "theorem side_pos (b : Box) : 0 < b.side := b"):
            with self.subTest(snippet=snippet):
                declaration = "Notes.Box" if snippet.startswith("structure") else "Notes.Box.volume"
                self.assertIsNone(self.statement(declaration, snippet))

    def test_a_goal_view_is_never_read_and_a_space_inside_a_token_stays_in_it(self):
        snippet = 'def two : ℕ := by\n  exact "a b"'
        runs = self.statement("Notes.Box.two", snippet)
        self.assertEqual(joined(runs), snippet)
        self.assertIn(["kw", "by", 40], runs)
        self.assertIn(["lit", '"a b"', 52], runs)
        value, _counts = self.extract(lean_end("Notes.Box.two", snippet))
        self.assertNotIn("41", value["hovers"], "the goal view's hovers are not the statement's")

    def test_an_ellipsis_inside_the_snippet_is_no_statement_even_where_the_page_has_one(self):
        self.assertIsNotNone(self.statement("Notes.Box.three", "def three := 3"))
        self.assertIsNone(self.statement("Notes.Box.dots", 'def dots := "…"'))

    def test_a_message_the_page_shows_in_its_code_is_not_code(self):
        runs = self.statement("Notes.Box.three", "def three := 3")
        self.assertEqual(runs, [["kw", "def"], ["tx", " "], ["const", "three"], ["tx", " := "], ["lit", "3"]])

    def test_two_code_elements_are_two_lines(self):
        runs = self.statement("Notes.Box.uno", "def uno := 1")
        self.assertEqual(joined(runs), "def uno := 1")
        self.assertEqual(runs[0], ["kw", "def"])

    def test_a_tagged_example_is_found_by_its_tag_on_its_module_page(self):
        snippet = "example : 0 < Box := trivial"
        value, counts = self.extract(example_end(EXAMPLE_TAG, snippet))
        self.assertEqual(counts, {"statements": 1, "targets": 1})
        [runs] = value["statements"][f"{BOX_PAGE}#{EXAMPLE_TAG}"]
        self.assertEqual(joined(runs), snippet)
        self.assertEqual(runs[0], ["kw", "example"])
        self.assertIn(["const", "Box", 12, None, "Notes/Box/index.html#Notes___Box"], runs)
        [nested] = self.extract(example_end(NESTED_TAG, "example : True := trivial"))[0]["statements"].values()
        self.assertEqual(nested[0][:2], [["kw", "example"], ["tx", " : "]],
                         "a tag inside the last tactic before it: the goal view is not read")
        self.assertEqual(joined(nested[0]), "example : True := trivial")
        for tag, text in ((EXAMPLE_TAG, "example : 1 < Box := trivial"), ("example:notes-box-gone", snippet)):
            with self.subTest(tag=tag, text=text):
                self.assertEqual(self.extract(example_end(tag, text))[0]["statements"], {})

    def test_a_link_goes_only_to_a_declarations_own_anchor(self):
        runs = self.statement("Notes.Box.sets", "def sets : Set carrier")
        self.assertIn(["const", "Set", 71], runs, "an id on a use of Set is not where Set is declared")
        self.assertIn(["const", "carrier", 70, None, "Notes/Geo/index.html#Notes___Geo___carrier"], runs)
        site = VERSO.Site(self.site)
        self.addCleanup(site.close)
        self.assertEqual([site.declares(BOX_PAGE, fragment) for fragment in
                          ("Notes___Box", "Notes___Box___side", "Notes___Box___volume", "Notes___Box___nope")],
                         [True, True, True, False], "a field of a declared structure is declared too")

    def test_a_link_off_the_site_or_out_of_its_root_is_dropped_by_link_itself(self):
        site = AnySite()
        self.assertEqual(VERSO.link(site, BOX_PAGE, "Notes/Box/#Notes___Box"), "Notes/Box/index.html#Notes___Box")
        for href in ("../../../outside/index.html#Notes___Box", "../outside/", "/../outside/index.html#x",
                     "https://elsewhere.invalid/Notes/Box/index.html#Notes___Box", "//elsewhere.invalid/x#y",
                     "javascript:x#y", "Notes\\Box#Notes___Box", "Notes/Box/?q=1#Notes___Box"):
            with self.subTest(href=href):
                self.assertIsNone(VERSO.link(site, BOX_PAGE, href))

    def test_a_link_with_no_fragment_or_a_nul_is_no_link(self):
        for href in ("Notes/Box/", BOX_PAGE, "Notes/Box/#", VERSO.DOCS_NAME):
            with self.subTest(href=href):
                self.assertIsNone(VERSO.link(AnySite(), BOX_PAGE, href),
                                  "a page is not a declaration's own anchor")
        self.write("Notes/Plain/index.html", page(box(block(
            tok("keyword", "def"), gap(), tok("const", "plain", 64, ident="Notes___Plain___plain"), gap(),
            tok("built-in delim", ":"), gap(), link(VERSO.DOCS_NAME, tok("const", "A", 65)), gap(),
            link("Notes/Box/", tok("const", "B", 66)), gap(),
            link("Notes/%00x#Notes___Box___volume", tok("const", "C", 67)), gap(),
            link("Notes/Box/#Notes___Box", tok("const", "Box", 12))))))
        value, _ = self.extract(lean_end("Notes.Plain.plain", "def plain : A B C Box", module="Notes.Plain"))
        [runs] = value["statements"]["Notes/Plain/index.html#Notes___Plain___plain"]
        self.assertEqual([run for run in runs if len(run) == 5],
                         [["const", "Box", 12, None, "Notes/Box/index.html#Notes___Box"]])

    def test_a_card_whose_page_is_not_a_page_of_the_site_gets_none(self):
        outside = self.site.parent / "outside" / "index.html"
        outside.parent.mkdir()
        outside.write_text(page(ONE_ELEMENT), encoding="utf-8")
        self.assertIsNone(VERSO.site_file("../outside/index.html"))
        self.assertIsNone(VERSO.site_file("Notes/../../outside/index.html"))
        site = VERSO.Site(self.site)
        self.addCleanup(site.close)
        for relative in ("../outside/index.html", "Notes/../../outside/index.html", str(outside)):
            with self.subTest(relative=relative):
                self.assertIsNone(site.resolve(relative), "the root is never left, even to a file that exists")
                self.assertFalse(site.is_directory(relative.rpartition("/")[0]))
        self.assertIsNone(site.resolve("Notes/\0/index.html"))
        self.assertFalse(site.is_directory("Notes/\0"))
        for destination in ({"href": "https://notes.example.invalid/Box.html"}, {"href": "../outside/index.html"},
                            {"href": "Notes/../../outside/index.html"}, {"href": "Notes/%00/index.html"},
                            {"resolution": "fallback"}, {"href": "Notes/Gone/index.html"}):
            with self.subTest(destination=destination):
                value, counts = self.extract(lean_end("Notes.Box.volume", "noncomputable def volume", **destination))
                self.assertEqual(value["statements"], {})
                self.assertEqual(counts["statements"], 0)


class TestTheFile(VersoCase):
    def test_only_the_hovers_the_statements_use_are_copied_split_into_signature_and_doc(self):
        value, counts = self.extract(
            lean_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side"),
            lean_end("Notes.Box.volume", "noncomputable def volume (b : Box) : ℝ := nope"))
        self.assertEqual(counts, {"statements": 1, "targets": 2})
        self.assertEqual(value["schema"], VERSO.SCHEMA)
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(sorted(value["hovers"], key=int), ["10", "11", "12", "13", "14"])
        self.assertEqual(value["hovers"]["10"], {"signature": "Notes.Box.side_pos (b : Box) : 0 < b.side",
                                                 "doc": "The side is positive."})
        self.assertEqual(value["hovers"]["12"]["doc"], "A box.\n\nIts side is `side`.")
        self.assertEqual(value["hovers"]["11"], {"signature": "Box"})

    def test_a_site_with_no_hover_table_still_gets_its_tokens(self):
        (self.site / VERSO.DOCS_NAME).unlink()
        runs = self.statement("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side")
        self.assertEqual(runs[2], ["const", "side_pos"])
        self.assertIn(["const", "Box", None, None, "Notes/Box/index.html#Notes___Box"], runs)

    def test_two_snippets_of_one_declaration_are_two_statements_and_the_output_is_stable(self):
        ends = (lean_end("Notes.Box.volume", "noncomputable def volume (b : Box) : ℝ :=\n  b.side\n…"),
                lean_end("Notes.Box.volume", "noncomputable def volume (b : Box) : ℝ"))
        first, _ = self.extract(*ends)
        self.assertEqual(len(first["statements"][f"{BOX_PAGE}#Notes___Box___volume"]), 2)
        self.assertEqual(VERSO.dumps(first), VERSO.dumps(self.extract(*ends)[0]))
        self.assertTrue(VERSO.dumps(first).endswith(b"\n"))
        self.assertEqual(VERSO.dumps(first).count(b"\n"), 1, "one line")

    def test_a_page_reached_through_a_symlink_is_not_read(self):
        outside = Path(self.site.parent) / "outside"
        outside.mkdir()
        (outside / "index.html").write_text(page(PER_LINE), encoding="utf-8")
        (self.site / "Notes" / "Link").symlink_to(outside, target_is_directory=True)
        value, _ = self.extract(lean_end("Notes.Box", "structure Box where", href="Notes/Link/index.html"))
        self.assertEqual(value["statements"], {})

    def test_the_key_is_the_one_the_viewer_builds(self):
        table = json.loads(TARGETS.read_text(encoding="utf-8"))
        self.assertEqual(table["namespace"], NAMESPACE)
        for case in table["cases"]:
            with self.subTest(case=case):
                ext = {key: case[key] for key in ("declaration", "destination") if key in case}
                end = {"location": case["location"], "extensions": {NAMESPACE: ext}}
                self.assertEqual(VERSO.card_target(end), case["target"])



# ---------------------------------------------------------------------------
# a Verso build elsewhere: `--pages`, and the file a companion commits
# ---------------------------------------------------------------------------

REMOTE = "https://git.example.invalid/notes/blob/main"
HIDDEN_PAGE = "Notes/Hidden/index.html"
#: Names Verso spells out or mangles, and a private declaration whose anchor only the cross-reference table gives.
HIDDEN = box(
    block(tok("keyword", "def"), gap(), tok("const", "«x y»", 110, ident="Notes___Hidden____FLQQ_x-y_FLQQ_"), gap(),
          tok("built-in delim", ":="), gap(), tok("literal number", "1")),
    block(tok("keyword", "def"), gap(), tok("const", "two'", 111, ident="Notes___Hidden___two___"), gap(),
          tok("built-in delim", ":="), gap(), tok("literal number", "2")),
    block(tok("keyword", "private"), gap(), tok("keyword", "theorem"), gap(),
          tok("const", "secret", 112, ident="_private___0___Notes___Hidden___secret"), gap(), tok("built-in delim", ":"),
          gap(), link("Notes/Box/#Notes___Box", tok("const", "True", 32)), gap(), tok("built-in delim", ":="), gap(),
          tok("const", "trivial", 33)),
)
XREF = {"VersoHtml.constant": {"contents": {
    "_private.0.Notes.Hidden.secret": [{"address": "/Notes/Hidden/", "id": "_private___0___Notes___Hidden___secret",
                                        "data": {"module": "Notes.Hidden", "private": True,
                                                 "userName": "Notes.Hidden.secret"}}],
    "Notes.Hidden.two'": [{"address": "/Notes/Geo/", "id": "Notes___Geo___carrier",
                           "data": {"module": "Notes.Hidden", "private": False, "userName": "Notes.Hidden.two'"}}],
    "Notes.Hidden.«x y»": [{"address": "/Notes/Hidden/", "id": anchor,
                            "data": {"module": "Notes.Hidden", "private": False, "userName": "Notes.Hidden.«x y»"}}
                           for anchor in ("Notes___Hidden___two___", "_private___0___Notes___Hidden___secret")],
}}}
#: The commit the build's pages were made from, which the cards pin.
BUILD_COMMIT = "c0ffee" + "0" * 34


def remote_end(declaration: str, snippet: str, *, module: str = "Notes.Box", line: int = 1,
               version: str = BUILD_COMMIT, **declared) -> dict:
    """An end whose card links its source file elsewhere, as a companion with no Verso site has it."""
    ext = {"destination": {"resolution": "exact", "href": f"{REMOTE}/{module.replace('.', '/')}.lean",
                           "fragment": f"L{line}"},
           "snippet": {"line": line, "text": snippet}}
    if declared:
        ext["declaration"] = declared
    location = {"declaration": declaration, "module": module} if declaration else {"lines": [line], "module": module}
    return {"type": "lean", "uri": "https://git.example.invalid/notes", "version": version, "role": "key",
            "location": location, "extensions": {NAMESPACE: ext}}


def remote_key(module: str, line: int) -> str:
    return f"{REMOTE}/{module.replace('.', '/')}.lean#L{line}"


class PagesCase(VersoCase):
    """The pages of a project's own Verso build, `<project>/.lake/build/literate-html`: not the viewer's site."""

    ROOT = ("project", ".lake", "build", "literate-html")
    COMMIT = BUILD_COMMIT

    def setUp(self) -> None:
        super().setUp()
        self.write(HIDDEN_PAGE, page(HIDDEN))
        self.write(VERSO.XREF_NAME, json.dumps(XREF))
        self.write(VERSO.DOCS_NAME, json.dumps({**DOCS, "112": "<code>Notes.Hidden.secret : True</code>"}))
        manifest = {"version": "1.2.0", "packages": [
            {"name": "mathlib", "rev": "1" * 40, "inputRev": "v0.0.0-test"},
            {"name": "verso", "rev": "2" * 40, "inputRev": "v0.0.0-test", "type": "git"}]}
        (self.site.parent.parent.parent / "lake-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def pages(self, *ends: dict, commit: str | None = BUILD_COMMIT) -> tuple[dict, dict]:
        return VERSO.extract_pages(self.site, entries(*ends), commit=commit)


class TestPagesOfABuildElsewhere(PagesCase):
    def test_a_card_is_found_by_its_module_and_declaration_and_filed_under_its_own_key(self):
        snippet = "noncomputable def volume (b : Box) : ℝ :=\n  b.side * b.side + zero"
        value, counts = self.pages(remote_end("Notes.Box.volume", snippet, line=9),
                                   remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3))
        self.assertEqual(counts, {"statements": 2, "targets": 2})
        self.assertEqual(sorted(value["statements"]), [remote_key("Notes.Box", 3), remote_key("Notes.Box", 9)])
        [runs] = value["statements"][remote_key("Notes.Box", 9)]
        self.assertEqual(joined(runs), snippet)
        self.assertEqual(runs[4], ["const", "volume", 20])
        self.assertIn(["var", "b", 21, 2], runs, "bindings and hovers as on the viewer's own site")
        self.assertEqual(value["hovers"]["10"]["doc"], "The side is positive.")

    def test_no_token_keeps_a_link_into_the_other_build(self):
        value, _ = self.pages(remote_end("Notes.Box.volume", "noncomputable def volume (b : Box) : ℝ", line=9),
                              remote_end("Notes.Box.sets", "def sets : Set carrier", line=5),
                              remote_end("Notes.Hidden.secret", "private theorem secret : True := trivial",
                                         module="Notes.Hidden", line=4))
        runs = [run for alternatives in value["statements"].values() for statement in alternatives for run in statement]
        self.assertEqual(len(value["statements"]), 3)
        self.assertIn(["const", "Box", 12], runs, "the link Box has on the page is dropped")
        self.assertIn(["const", "carrier", 70], runs)
        self.assertEqual([run for run in runs if len(run) > 4], [])
        self.assertEqual(VERSO.committed_problems(value), [])

    def test_the_anchor_is_verso_slug_of_the_name_or_what_the_cross_reference_table_files(self):
        self.assertEqual([VERSO.verso_slug(name) for name in
                          ("Notes.Box.volume", "Notes.Hidden.«x y»", "Notes.two'", "a→b", "f⟨x⟩[i]", "ℝ", "x\ty")],
                         ["Notes___Box___volume", "Notes___Hidden____FLQQ_x-y_FLQQ_", "Notes___two___", "a_ARR_b",
                          "f_LANGLE_x_RANGLE__LSQ_i_RSQ_", "___", "x-y"])
        self.assertEqual(VERSO.module_page("Notes.Hidden"), HIDDEN_PAGE)
        for module in ("", "Notes..Box", "Notes./x", "Notes.\\x"):
            with self.subTest(module=module):
                self.assertIsNone(VERSO.module_page(module))
        ends = (remote_end("Notes.Hidden.«x y»", "def «x y» := 1", module="Notes.Hidden", line=1),
                remote_end("Notes.Hidden.two'", "def two' := 2", module="Notes.Hidden", line=2),
                remote_end("Notes.Hidden.secret", "private theorem secret : True := trivial", module="Notes.Hidden",
                           line=4))
        self.assertEqual(VERSO.declaration_ids(VERSO.Site(self.site)),
                         {("Notes.Hidden.secret", "Notes.Hidden"): "_private___0___Notes___Hidden___secret"},
                         "a name filed under two anchors on its page is not used either")
        value, counts = self.pages(*ends)
        self.assertEqual(counts["statements"], 3, "an entry on another page than the module's is not used")
        self.assertEqual(value["statements"][remote_key("Notes.Hidden", 4)][0][4], ["const", "secret", 112])
        (self.site / VERSO.XREF_NAME).unlink()
        value, counts = self.pages(*ends)
        self.assertEqual(counts["statements"], 2, "without the table, a private declaration's anchor is not known")
        self.assertNotIn(remote_key("Notes.Hidden", 4), value["statements"])

    def test_a_missing_page_or_anchor_or_a_snippet_that_does_not_match_gives_none(self):
        for end in (remote_end("Notes.Gone.volume", "def volume", module="Notes.Gone"),
                    remote_end("Notes.Box.nope", "def nope := 1"),
                    remote_end("Notes.Box.volume", "noncomputable def volume (b : Box) : ℕ"),
                    remote_end("Notes.Box.volume", "noncomputable def volume", module="Notes..Box"),
                    remote_end("", "example : 0 < Box := trivial", kind="example", id="example:notes-box-gone")):
            with self.subTest(end=end["location"]):
                value, counts = self.pages(end)
                self.assertEqual((value["statements"], counts["statements"]), ({}, 0))

    def test_a_tagged_example_is_found_by_its_tag_on_its_modules_page(self):
        snippet = "example : 0 < Box := trivial"
        value, counts = self.pages(remote_end("", snippet, line=7, kind="example", id=EXAMPLE_TAG, anonymous=True))
        self.assertEqual(counts, {"statements": 1, "targets": 1})
        [runs] = value["statements"][remote_key("Notes.Box", 7)]
        self.assertEqual(joined(runs), snippet)
        self.assertIn(["const", "Box", 12], runs)

    def test_the_file_records_the_project_commit_and_the_builds_verso(self):
        end = remote_end("Notes.Box.three", "def three := 3")
        value, _ = self.pages(end, commit=self.COMMIT)
        self.assertEqual(value["build"], {"commit": self.COMMIT, "verso": {"inputRev": "v0.0.0-test", "rev": "2" * 40}})
        project = self.site.parent.parent.parent
        self.assertEqual(VERSO.build_record(project / "out" / "html" / "literate-html", self.COMMIT),
                         {"commit": self.COMMIT}, "pages outside the project's .lake/build name no Verso of its")
        (project / "lake-manifest.json").unlink()
        self.assertEqual(self.pages(end, commit=self.COMMIT)[0]["build"], {"commit": self.COMMIT})
        uncommitted = self.pages(end, commit=None)[0]
        self.assertNotIn("build", uncommitted)
        self.assertEqual([line[:6] for line in VERSO.committed_problems(uncommitted)], ["build:"],
                         "a committed file must say which commit its build is of")

    def test_dependency_pages_keep_their_own_hovers_and_pinned_revision(self):
        repo = "https://git.example.invalid/dependency"
        project = self.site.parent.parent.parent
        manifest = project / "lake-manifest.json"
        data = json.loads(manifest.read_text())
        data["packages"].append({"name": "dependency", "type": "git", "url": repo + ".git",
                                 "rev": "d" * 40})
        manifest.write_text(json.dumps(data))
        dependency = project / ".lake/packages/dependency/.lake/build/literate-html"
        module_page = dependency / "Dependency/Box/index.html"
        module_page.parent.mkdir(parents=True)
        module_page.write_text(page(box(block(
            tok("keyword", "def"), gap(), tok("const", "three", 10, ident="Dependency___Box___three"),
            gap(" := 3")))))
        (dependency / VERSO.DOCS_NAME).write_text(json.dumps({"10": "<code>Dependency.Box.three : Nat</code>"}))
        own = remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3)
        dep = remote_end("Dependency.Box.three", "def three := 3", module="Dependency.Box",
                         version="d" * 40)
        dep["uri"] = repo
        value, counts = VERSO.extract_pages(self.site, entries(own, dep), commit=self.COMMIT,
                                            dependency_pages={repo: dependency})
        self.assertEqual(value["build"]["dependencies"], {repo: "d" * 40})
        self.assertEqual(counts, {"statements": 2, "targets": 2})
        self.assertEqual(VERSO.committed_problems(value), [])
        self.assertIsNone(VERSO.unpinned(value, entries(own, dep)))
        kept, counts = VERSO.joined(value, entries(own, dep))
        self.assertEqual(counts["dropped"], 0)
        self.assertEqual(kept["hovers"]["10"]["signature"], "Notes.Box.side_pos (b : Box) : 0 < b.side")
        [runs] = kept["statements"][VERSO.card_target(dep)]
        hover_id = next(str(r[2]) for r in runs if r[0] == "const" and r[1] == "three")
        self.assertNotEqual(hover_id, "10", "different page builds can reuse hover IDs")
        self.assertEqual(kept["hovers"][hover_id]["signature"], "Dependency.Box.three : Nat")
        dep["version"] = "e" * 40
        cut, counts = VERSO.joined(value, entries(own, dep))
        self.assertEqual(counts["dropped"], 1)
        self.assertNotIn(VERSO.card_target(dep), cut["statements"])
        self.assertNotIn(hover_id, cut["hovers"])
        with self.assertRaises(VERSO.overlay_annotations.AnnotationError):
            VERSO.extract_pages(self.site, entries(own, dep), commit=self.COMMIT,
                                dependency_pages={"https://git.example.invalid/unknown": dependency})

    def test_the_command_writes_the_committed_form_one_entry_a_line(self):
        ends = (remote_end("Notes.Box.three", "def three := 3"), remote_end("Notes.Box.uno", "def uno := 1", line=2))
        bundle = self.site.parent / "annotations.bundle.json"
        bundle.write_text(json.dumps({"schema": VERSO.overlay_annotations.BUNDLE_SCHEMA, "schema_version": 1,
                                      "directory": "annotations", "count": 2,
                                      "annotations": [{"path": p, "annotation": a} for p, a in entries(*ends)]}),
                          encoding="utf-8")
        out = self.site.parent / "verso.json"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(VERSO.main(["--pages", str(self.site), "--bundle", str(bundle), "--out", str(out),
                                         "--commit", self.COMMIT]), 0)
        self.assertEqual(json.loads(printed.getvalue())["statements"], 2)
        raw = out.read_bytes()
        value, _ = self.pages(*ends, commit=self.COMMIT)
        self.assertEqual(json.loads(raw), value)
        self.assertEqual(raw, VERSO.dumps_lines(value))
        self.assertEqual(sum(line.startswith(b'"' + VERSO_KEY_START) for line in raw.splitlines()), 2,
                         "each target's statements on a line of its own")
        command = ["--pages", str(self.site), "--bundle", str(bundle), "--out", str(out)]
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in ([], ["--commit", "HEAD"], ["--commit", "abc123"]):
                with self.subTest(argv=argv), self.assertRaises(SystemExit):
                    VERSO.main([*command, *argv])
        real = self.site.parent / "real.json"
        real.write_text("kept", encoding="utf-8")
        linked = self.site.parent / "linked.json"
        linked.symlink_to(real)
        with contextlib.redirect_stderr(io.StringIO()) as said:
            self.assertEqual(VERSO.main([*command[:-1], str(linked), "--commit", self.COMMIT]), 1)
        self.assertIn("STOP", said.getvalue())
        self.assertEqual((linked.is_symlink(), real.read_text(encoding="utf-8")), (True, "kept"),
                         "never written through a symlink")
        rows = json.loads(bundle.read_text(encoding="utf-8"))
        for value in ({}, {**rows, "schema": "formal-companion/overlay-generated"}):
            bundle.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(bundle=sorted(value)), contextlib.redirect_stderr(io.StringIO()) as said:
                self.assertEqual(VERSO.main([*command, "--commit", self.COMMIT]), 1)
                self.assertIn("bundle", said.getvalue())

    def test_the_command_writes_nothing_every_check_would_refuse_for_a_card_that_pins_a_tag(self):
        bundle = self.site.parent / "annotations.bundle.json"
        out = self.site.parent / "verso.json"

        def command(*ends: dict) -> tuple[int, str]:
            bundle.write_text(json.dumps({"schema": VERSO.overlay_annotations.BUNDLE_SCHEMA, "schema_version": 1,
                                          "directory": "annotations", "count": len(ends),
                                          "annotations": [{"path": p, "annotation": a} for p, a in entries(*ends)]}),
                              encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as said, contextlib.redirect_stdout(io.StringIO()):
                code = VERSO.main(["--pages", str(self.site), "--bundle", str(bundle), "--out", str(out),
                                   "--commit", self.COMMIT])
            return code, said.getvalue()

        three = remote_end("Notes.Box.three", "def three := 3", line=8)
        uno = remote_end("Notes.Box.uno", "def uno := 1", line=2, version="v1.0.0")
        code, said = command(three, uno)
        self.assertEqual(code, 1, "the statement it cut for the tagged card could not be held to the build")
        self.assertIn("STOP", said)
        self.assertIn("'v1.0.0'", said)
        self.assertIn("annotations/1.1/01.json", said)
        self.assertFalse(out.exists())
        elsewhere = remote_end("Mathlib.Order.le_refl", "theorem le_refl", module="Mathlib.Order", version="v4.33.0")
        self.assertEqual(command(three, elsewhere), (0, ""), "no page of this build holds it, so nothing is lost")
        self.assertTrue(out.exists())


VERSO_KEY_START = REMOTE.encode("utf-8")


class TestTheCommittedFile(PagesCase):
    def committed(self) -> dict:
        return self.pages(remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3),
                          remote_end("Notes.Box.three", "def three := 3", line=8), commit=self.COMMIT)[0]

    def test_what_pages_writes_has_no_problem_and_anything_else_is_named(self):
        value = self.committed()
        self.assertEqual(VERSO.committed_problems(value), [])
        key = remote_key("Notes.Box", 3)

        def changed(change) -> list[str]:
            copy = json.loads(json.dumps(value))
            change(copy)
            return VERSO.committed_problems(copy)

        cases = (
            ("a run carries a link", lambda v: v["statements"][key][0].append(["const", "Box", None, None, "Box/#B"])),
            ("is not in hovers", lambda v: v["hovers"].pop("10")),
            ("not a known kind", lambda v: v["statements"][key][0].append(["html", "<b>"])),
            ("not a known kind", lambda v: v["statements"][key][0].append(["tx", ""])),
            ("not a known kind", lambda v: v["statements"][key][0].append([["kw"], "def"])),
            ("not a known kind", lambda v: v["statements"][key][0].append([{"kw": 1}, "def"])),
            ("binding", lambda v: v["statements"][key][0].append(["var", "b", None, 0])),
            ("expected schema", lambda v: v.update(schema_version=2)),
            ("unknown key", lambda v: v.update(links={})),
            ("hovers['x']", lambda v: v["hovers"].update(x={"signature": "s"})),
            ("hovers['10']", lambda v: v["hovers"]["10"].update(html="<b>B</b>")),
            ("hovers['10']", lambda v: v["hovers"]["10"].update(doc="")),
            ("build", lambda v: v["build"].update(commit="HEAD")),
            ("build", lambda v: v["build"].pop("commit")),
            ("build.dependencies", lambda v: v["build"].update(dependencies=[])),
            ("build.dependencies", lambda v: v["build"].update(dependencies={"https://git.example.invalid/lib": "tag"})),
            ("build.dependencies", lambda v: v["build"].update(dependencies={"http://git.example.invalid/lib": "a" * 40})),
            ("build.dependencies", lambda v: v["build"].update(dependencies={"https://[": "a" * 40})),
            ("build", lambda v: v.pop("build")),
            ("a list of statements", lambda v: v["statements"].update({key: []})),
        )
        for words, change in cases:
            with self.subTest(words=words):
                self.assertTrue(any(words in line for line in changed(change)), changed(change))
        self.assertEqual(VERSO.committed_problems([]), ["expected an object"])
        many = changed(lambda v: v["hovers"].update({str(n): {} for n in range(200, 230)}))
        self.assertEqual((len(many), many[-1]), (11, "and 20 more"))

    def test_a_run_whose_kind_is_not_text_is_refused_and_reported_not_a_crash(self):
        value = self.committed()
        value["statements"][remote_key("Notes.Box", 3)][0][0][0] = ["kw"]
        root = Path(tempfile.mkdtemp(dir=self.site.parent))
        (root / "site").mkdir()
        (root / "pdf-overlay").mkdir()
        (root / "pdf-overlay" / VERSO.FILE_NAME).write_bytes(VERSO.dumps_lines(value))
        build, check = load_tool("build_site"), load_tool("check_generated")
        with self.assertRaisesRegex(build.BuildError, r"a run is not a known kind .*; write it again with"):
            build.verso_file(root / "pdf-overlay", root / "site", [])
        self.assertEqual(check.verso_problems(root / "pdf-overlay"),
                         [f"{VERSO.FILE_NAME}: statements[{remote_key('Notes.Box', 3)!r}]: "
                          "a run is not a known kind with its text: [['kw'], 'theorem']"])

    def test_joined_keeps_a_statement_only_while_its_card_has_that_snippet(self):
        value = self.committed()
        ends = [remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3),
                remote_end("Notes.Box.three", "def three := 3", line=8)]
        same, counts = VERSO.joined(value, entries(*ends))
        self.assertEqual((same, counts), (value, {"dropped": 0, "statements": 2, "targets": 2}))
        ends[0] = remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 1 < b.side", line=3)
        cut, counts = VERSO.joined(value, entries(*ends))
        self.assertEqual(counts, {"dropped": 1, "statements": 1, "targets": 2})
        self.assertEqual(list(cut["statements"]), [remote_key("Notes.Box", 8)])
        self.assertEqual(cut["hovers"], {}, "the hovers only the dropped statement used go with it")
        self.assertEqual(cut["build"], value["build"])
        moved, counts = VERSO.joined(value, entries(remote_end("Notes.Box.three", "def three := 3", line=9)))
        self.assertEqual((moved["statements"], counts["dropped"]), ({}, 2), "a card filed under another key")

    def test_a_committed_file_is_of_the_commit_its_cards_pin(self):
        value = self.committed()
        other = "f" * 40
        ends = [remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3),
                remote_end("Notes.Box.three", "def three := 3", line=8)]
        self.assertEqual(VERSO.pinned(entries(*ends)), {BUILD_COMMIT})
        self.assertIsNone(VERSO.unpinned(value, entries(*ends)))
        self.assertIsNone(VERSO.unpinned(value, []), "no card, nothing to be stale against")
        ends[1] = remote_end("Notes.Box.three", "def three := 3", line=8, version=other)
        self.assertIsNone(VERSO.unpinned(value, entries(*ends)), "one card still pins the build's commit")
        cut, counts = VERSO.joined(value, entries(*ends))
        self.assertEqual(counts, {"dropped": 1, "statements": 1, "targets": 2})
        self.assertEqual(list(cut["statements"]), [remote_key("Notes.Box", 3)],
                         "a card that pins another commit keeps no statement of this build")
        ends[0] = remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3, version=other)
        self.assertEqual(VERSO.unpinned(value, entries(*ends)),
                         f"cut from a build of {BUILD_COMMIT}, but the cards pin {other}; write it again with "
                         "`verso_extract.py --pages` from a build of that commit")
        many = [remote_end("Notes.Box.three", "def three := 3", line=8, version=f"{n}" * 40) for n in range(1, 6)]
        self.assertIn("and 2 more", VERSO.unpinned(value, entries(*many)))

    @staticmethod
    def pinning(*versions: str | None) -> list[tuple[str, dict]]:
        """The two cards `committed` cuts statements for, pinning these; None for no `version`."""
        ends = [remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3),
                remote_end("Notes.Box.three", "def three := 3", line=8)]
        for end, version in zip(ends, versions):
            end["version"] = version
            if version is None:
                del end["version"]
        return entries(*ends)

    def test_a_card_that_pins_a_tag_is_refused_by_name_when_only_some_do(self):
        value = self.committed()
        problem = VERSO.unpinned(value, self.pinning(BUILD_COMMIT, "v1.0.0"))
        self.assertIsNotNone(problem, "joined would drop its statement with nothing said")
        self.assertIn("'v1.0.0'", problem)
        self.assertIn("annotations/1.1/01.json", problem)
        self.assertNotIn("annotations/1.1/00.json", problem, "the card that pins the build's commit is not named")
        self.assertIn("git rev-parse", problem)

    def test_a_card_that_pins_a_tag_is_told_what_works_when_all_do(self):
        problem = VERSO.unpinned(self.committed(), self.pinning("v1.0.0", "v1.0.0"))
        self.assertIn("'v1.0.0'", problem)
        self.assertIn("annotations/1.1/00.json, annotations/1.1/01.json", problem)
        self.assertIn("git rev-parse", problem)
        self.assertNotIn("from a build of that commit", problem, "--commit refuses a tag, so that cannot work")

    def test_what_the_refusal_says_settles_it(self):
        value = self.committed()
        self.assertIsNone(VERSO.unpinned(value, self.pinning(BUILD_COMMIT, BUILD_COMMIT)))
        kept, counts = VERSO.joined(value, self.pinning(BUILD_COMMIT, BUILD_COMMIT))
        self.assertEqual((kept, counts["dropped"]), (value, 0), "the tag named the build's commit: nothing else to do")
        other = "f" * 40
        problem = VERSO.unpinned(value, self.pinning(other, other))
        self.assertEqual(problem, f"cut from a build of {BUILD_COMMIT}, but the cards pin {other}; write it again "
                                  "with `verso_extract.py --pages` from a build of that commit")
        again = self.pages(remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3),
                           remote_end("Notes.Box.three", "def three := 3", line=8), commit=other)[0]
        self.assertIsNone(VERSO.unpinned(again, self.pinning(other, other)), "and --commit takes that commit")
        elsewhere = remote_end("Mathlib.Order.le_refl", "theorem le_refl", module="Mathlib.Order", version="v4.33.0")
        self.assertEqual(VERSO.unpinned(value, [*self.pinning(other, other), *entries(elsewhere)]), problem,
                         "a build of a tag cannot be asked for, so it names the commits alone")

    def test_anything_but_a_full_lowercase_commit_is_refused_and_so_is_no_version(self):
        value = self.committed()
        for version in ("main", "abc1234", "A" * 40, "a" * 39, "a" * 41, "v4.33.0-rc1", f"{BUILD_COMMIT}\n"):
            with self.subTest(version=version):
                self.assertIn(repr(version), VERSO.unpinned(value, self.pinning(BUILD_COMMIT, version)))
        self.assertIn("no version", VERSO.unpinned(value, self.pinning(BUILD_COMMIT, None)))
        self.assertIn("no version", VERSO.unpinned(value, self.pinning(None, None)))
        self.assertIn("cut from a build", VERSO.unpinned(value, self.pinning("a" * 64, "a" * 64)),
                      "a sha-256 commit is a commit")

    def test_many_cards_and_tags_are_named_up_to_a_point(self):
        tags = [f"v{n}.0.0" for n in range(5)]
        value = self.pages(*(remote_end("Notes.Box.three", "def three := 3", line=8, version=tag) for tag in tags),
                           commit=self.COMMIT)[0]
        problem = VERSO.unpinned(value, entries(*(remote_end("Notes.Box.three", "def three := 3", line=8, version=tag)
                                                  for tag in tags)))
        self.assertIn("'v0.0.0', 'v1.0.0', 'v2.0.0' and 2 more", problem)
        self.assertIn("annotations/1.1/02.json and 2 more", problem)

    def test_a_card_with_no_statement_to_lose_may_pin_what_it_likes(self):
        value = self.committed()
        ends = [remote_end("Notes.Box.side_pos", "theorem side_pos (b : Box) : 0 < b.side", line=3),
                remote_end("Notes.Box.three", "def three := 3", line=8)]
        snippetless = remote_end("Notes.Box.uno", "def uno := 1", line=2, version="v1.0.0")
        snippetless["extensions"][NAMESPACE].pop("snippet")
        keyless = remote_end("Notes.Box.uno", "def uno := 1", line=2, version="v1.0.0")
        keyless["extensions"][NAMESPACE].pop("destination")
        for name, end in (("a declaration of another project, at its release",
                           remote_end("Mathlib.Order.le_refl", "theorem le_refl", module="Mathlib.Order", line=9,
                                      version="v4.33.0")),
                          ("a statement the file does not hold",
                           remote_end("Notes.Box.uno", "def uno := 1", line=2, version="v1.0.0")),
                          ("a snippet the file's statement no longer joins",
                           remote_end("Notes.Box.three", "def three := 4", line=8, version="v1.0.0")),
                          ("a statement another card keeps",
                           remote_end("Notes.Box.three", "def three := 3", line=8, version="v1.0.0")),
                          ("no snippet", snippetless), ("no key", keyless)):
            with self.subTest(name):
                self.assertIsNone(VERSO.unpinned(value, entries(*ends, end)))
                self.assertEqual(VERSO.joined(value, entries(*ends, end))[1]["dropped"], 0)

    def test_a_file_no_card_can_be_held_to_is_refused_with_nothing_to_lose(self):
        problem = VERSO.unpinned(self.committed(), entries(remote_end("Notes.Box.uno", "def uno := 1", line=2,
                                                                      version="v1.0.0")))
        self.assertIn("annotations/1.1/00.json", problem)
        self.assertIn("git rev-parse", problem)
        self.assertNotIn("from a build of that commit", problem)

    def test_a_statement_two_cards_find_alike_is_kept_once(self):
        snippet = "def «x y» := 1\ndef two' := 2"
        value, counts = self.pages(remote_end("Notes.Hidden.«x y»", snippet, module="Notes.Hidden", line=1),
                                   remote_end("Notes.Hidden.two'", snippet, module="Notes.Hidden", line=1))
        self.assertEqual(counts, {"statements": 1, "targets": 2})
        [runs] = value["statements"][remote_key("Notes.Hidden", 1)]
        self.assertEqual(joined(runs), snippet)

if __name__ == "__main__":                                   # pragma: no cover
    unittest.main(verbosity=2)
