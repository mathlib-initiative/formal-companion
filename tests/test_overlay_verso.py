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

import html
import importlib.util
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
    "99": "<code>never used</code>",
}


def lean_end(declaration: str, snippet: str, *, module: str = "Notes.Box", **destination) -> dict:
    destination.setdefault("resolution", "exact")
    return {"type": "lean", "uri": "https://git.example.invalid/notes", "role": "key",
            "location": {"declaration": declaration, "module": module},
            "extensions": {NAMESPACE: {"destination": destination, "snippet": {"line": 1, "text": snippet}}}}


def entries(*ends: dict) -> list[tuple[str, dict]]:
    return [(f"annotations/1.1/{n:02d}.json", {"id": f"item:{n}", "subject": [], "object": [end]})
            for n, end in enumerate(ends)]


def joined(runs: list[list]) -> str:
    return "".join(run[1] for run in runs)


class VersoCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="overlay-verso-test-")
        self.addCleanup(temporary.cleanup)
        self.site = Path(temporary.name) / "site"
        self.write(BOX_PAGE, page(prose("Boxes."), PER_LINE, ONE_ELEMENT, prose("Attributes."), ATTRIBUTED,
                                  TACTIC))
        self.write("Notes/Real/index.html", REAL_PAGE)
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

    def test_a_card_whose_page_is_not_a_page_of_the_site_gets_none(self):
        for destination in ({"href": "https://notes.example.invalid/Box.html"}, {"href": "../outside/index.html"},
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
                ext = {"destination": case["destination"]} if "destination" in case else {}
                end = {"location": case["location"], "extensions": {NAMESPACE: ext}}
                self.assertEqual(VERSO.card_target(end), case["target"])


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main(verbosity=2)
