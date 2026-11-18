"""A synthetic manuscript, its typeset rows and a roadmap that cites its lines.

New code, shared by tests/test_texplace.py and tests/test_export_roadmap_locators.py
(docs/exporters.md section 9).  Nothing here is of any real text: the manuscript is
twenty-four lines about pebbles, written for the tests, and its "PDF" is the eleven
rows a typesetter would break those lines into, given either as the rows themselves
(:func:`layout_pages`, which needs no poppler) or as a one-page PDF that prints them
(:func:`pdf_bytes`, for the tests that run poppler).

The line breaks of the source and of the rows are chosen so that every rule of
`annotations.export.texplace` has a case: a source line that ends in the right half
of a row and one that ends in the left half, a displayed formula between two
paragraphs, a labelled environment, a comment, and a page number.
"""
from __future__ import annotations

#: The manuscript.  SOURCE_LINES[n - 1] is line n.
SOURCE_LINES = [
    r"\documentclass{article}",                              # 1
    r"\begin{document}",                                     # 2
    r"\section{Coloured pebbles}\label{sec:pebbles}",        # 3
    r"A pebble is red when the stream wears it",             # 4
    r"smooth, and blue otherwise. Every heap of",            # 5
    r"pebbles has a largest red pebble.",                    # 6
    r"",                                                     # 7
    r"\begin{lemma}[Heaps]\label{lem:heaps}",                # 8
    r"A nonempty heap of pebbles has a heaviest",            # 9
    r"member.",                                              # 10
    r"\end{lemma}",                                          # 11
    r"\begin{proof}",                                        # 12
    r"Weigh the pebbles one after another and keep",         # 13
    r"the heavier of each pair. The count is",               # 14
    r"\[",                                                   # 15
    r"  n(n-1)/2 .",                                         # 16
    r"\]",                                                   # 17
    r"comparisons, which is finite. Hence the",              # 18
    r"heaviest pebble exists.",                              # 19
    r"\end{proof}",                                          # 20
    r"% a comment that prints nothing",                      # 21
    r"The river carries the lightest pebbles to",            # 22
    r"the delta, where the heap grows again.",               # 23
    r"\end{document}",                                       # 24
]
SOURCE = "\n".join(SOURCE_LINES) + "\n"

#: What the page prints, row by row; the last is the page number.
ROWS = [
    "1 Coloured pebbles",                                                  # 0
    "A pebble is red when the stream wears it smooth, and blue",           # 1
    "otherwise. Every heap of pebbles has a largest red pebble.",          # 2
    "Lemma 1 (Heaps). A nonempty heap of pebbles has a heaviest",          # 3
    "member.",                                                             # 4
    "Proof. Weigh the pebbles one after another and keep the heavier",     # 5
    "of each pair. The count is",                                          # 6
    "n(n - 1)/2.",                                                         # 7
    "comparisons, which is finite. Hence the heaviest pebble exists.",     # 8
    "The river carries the lightest pebbles",                              # 9
    "to the delta, where the heap grows again.",                           # 10
    "7",                                                                   # the folio
]
PAGE = (612.0, 792.0)
LEFT = 100.0
#: points per character of the rows :func:`layout_pages` gives
CHAR = 6.0
TOP = 100.0
SKIP = 14.0
HEIGHT = 10.0


def row_top(index: int) -> float:
    return TOP + SKIP * index


def line_of(top: float, text: str, left: float = LEFT) -> tuple:
    """One line of a page for `texplace.Layout`: each word where a fixed-width font puts it."""
    words, at = [], 0
    for word in text.split(" "):
        if word:
            words.append((left + CHAR * at, left + CHAR * (at + len(word)), word))
        at += len(word) + 1
    return (top, top + HEIGHT, words)


def layout_pages(rows: list[str] | None = None) -> list[tuple]:
    """The one page of the manuscript as `texplace.Layout` reads pages."""
    rows = ROWS if rows is None else rows
    return [(PAGE[0], PAGE[1], [line_of(row_top(i), text) for i, text in enumerate(rows)])]


def pdf_bytes(rows: list[str] | None = None) -> bytes:
    """A one-page PDF that prints the rows in Helvetica, one baseline skip apart."""
    rows = ROWS if rows is None else rows

    def escape(text: str) -> str:
        return "".join("\\" + c if c in "()\\" else c for c in text)

    content = "BT\n" + "".join(
        f"/F1 9 Tf 1 0 0 1 {LEFT:g} {PAGE[1] - row_top(i) - 8:g} Tm ({escape(text)}) Tj\n"
        for i, text in enumerate(rows)) + "ET\n"
    objects = ["<</Type/Catalog/Pages 2 0 R>>",
               "<</Type/Pages/Kids[3 0 R]/Count 1>>",
               f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {PAGE[0]:g} {PAGE[1]:g}]"
               "/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
               f"<</Length {len(content)}>>stream\n{content}endstream",
               "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>"]
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj{body}endobj\n"
    start = len(out)
    out += (f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
            + "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
            + f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{start}\n%%EOF\n")
    return out.encode("latin-1")


# ---------------------------------------------------------------------------
# the companion: a Lean library, a roadmap and its locator files
# ---------------------------------------------------------------------------

TEX_NAME = "notes.tex"

LEAN_ROOT = "import Pebbles.Basic\n"
#: Each declaration on a line of its own; LEAN_LINES names the lines the tests expect.
LEAN_BASIC = "\n".join([
    "/-! Pebbles and heaps of them. -/",                                   # 1
    "",                                                                    # 2
    "namespace Pebbles",                                                   # 3
    "",                                                                    # 4
    "/-- The two colours. -/",                                             # 5
    "inductive Colour where",                                              # 6
    "  | red",                                                             # 7
    "  | blue",                                                            # 8
    "",                                                                    # 9
    "/-- A heap of weights. -/",                                           # 10
    "structure Heap where",                                                # 11
    "  weights : List Nat",                                                # 12
    "  nonempty : weights ≠ []",                                           # 13
    "",                                                                    # 14
    "theorem heaviest_exists (h : Heap) :",                                # 15
    "    ∃ w ∈ h.weights, ∀ v ∈ h.weights, v ≤ w := by",                   # 16
    "  sorry",                                                             # 17
    "",                                                                    # 18
    "theorem weigh_le (a b : Nat) : min a b ≤ max a b := by",              # 19
    "  omega",                                                             # 20
    "",                                                                    # 21
    "private theorem hidden_step : True := trivial",                       # 22
    "",                                                                    # 23
    "theorem largest_redΔ : True := trivial",                              # 24
    "",                                                                    # 25
    "end Pebbles",                                                         # 26
]) + "\n"
LEAN_LINES = {"Pebbles.Colour": 6, "Pebbles.Heap": 11, "Pebbles.heaviest_exists": 15,
              "Pebbles.weigh_le": 19, "Pebbles.largest_redΔ": 24}


def locator_lines(tex_sha256: str | None) -> str:
    """The locator file of line ranges; with a sha256 its header pins the TeX file's bytes."""
    pin = f" (SHA-256 `{tex_sha256}`)" if tex_sha256 else ""
    return "\n".join([
        "# Locators for the notes",
        "",
        f"Line ranges refer to `{TEX_NAME}` in this repository{pin}. TeX labels are given",
        "where the item has one.",
        "",
        "## Pebbles",
        "",
        "Section 1 (`sec:pebbles`), lines 4-6: the two colours and the largest red pebble.",
        "",
        "## Heaps lemma",
        "",
        "`lem:heaps`, lines 8-11: the statement (lines 9-10) that a nonempty heap has a",
        "heaviest member.",
        "",
        "## Weighing",
        "",
        "Lines 13-17: the comparisons, and their count.",
        "",
        "## Conclusion",
        "",
        "Proof of `lem:heaps`, lines 18-19: finiteness and the conclusion.",
        "",
        "## Generator",
        "",
        "The script that draws the figure; it is no part of the notes.",
        "",
    ])


LOCATOR_LABELS = "\n".join([
    "# The notes",
    "",
    f"*Pebbles*, `{TEX_NAME}` in this repository.",
    "Locators are the TeX labels, which are stable across builds.",
    "",
    "## Lemma 1",
    "",
    "`lem:heaps`, Section 1: every nonempty heap has a heaviest member.",
    "",
])

LOCATOR_NOTES = "\n".join([
    "# Project notes",
    "",
    "The sketches are not in the notes; they are written for this project.",
    "",
    "## Sketch",
    "",
    "A drawing of two heaps.",
    "",
])


def page(title: str, *, lean: str | None, origin: str | None, sources: list[str],
         statement: str | None = "formalized") -> str:
    """One roadmap page."""
    front = ["---", "article_id: af_000000000000000000000000", "declaration: theorem"]
    if origin:
        front.append(f"origin: {origin}")
    if statement:
        front.append(f"statement: {statement}")
    if lean:
        front.append(f"lean: {lean}")
    front.append("---")
    body = ["", f"# {title}", "", "A statement.", "", "## Depends on", "", "None.", "", "## Sources", ""]
    body += [f"- [{target.rsplit('#', 1)[-1]}]({target})" for target in sources]
    return "\n".join(front + body) + "\n"


def repository_files(tex_sha256: str | None) -> dict[str, str]:
    """Every file of the companion, by its path in the checkout."""
    lines, labels, notes = ("../../sources/notes-locators.md", "../../sources/labels.md",
                            "../../sources/project-notes.md")
    return {
        TEX_NAME: SOURCE,
        "Pebbles.lean": LEAN_ROOT,
        "Pebbles/Basic.lean": LEAN_BASIC,
        "blueprint/README.md": "# Pebbles\n",
        "blueprint/sources/README.md": "# Sources\n",
        "blueprint/sources/notes-locators.md": locator_lines(tex_sha256),
        "blueprint/sources/labels.md": LOCATOR_LABELS,
        "blueprint/sources/project-notes.md": LOCATOR_NOTES,
        "blueprint/roadmap/README.md": "# Roadmap\n",
        "blueprint/roadmap/heaps/heaviest.md": page(
            "A heaviest member", lean="Pebbles.heaviest_exists, Pebbles.Heap", origin="cited",
            sources=[f"{lines}#heaps-lemma", f"{labels}#lemma-1"]),
        "blueprint/roadmap/heaps/weighing.md": page(
            "Weighing two pebbles", lean="Pebbles.weigh_le", origin="bridged",
            sources=[f"{lines}#weighing", f"{lines}#conclusion"]),
        "blueprint/roadmap/heaps/colours.md": page(
            "The two colours", lean="Pebbles.Colour, Pebbles.largest_redΔ, Pebbles.hidden_step, "
                                    "Pebbles.Heap.weights, Pebbles.gone",
            origin="cited", sources=[f"{lines}#pebbles", f"{notes}#sketch", f"{lines}#generator"]),
        "blueprint/roadmap/heaps/unnamed.md": page(
            "Not yet stated", lean=None, origin="cited", statement=None,
            sources=[f"{lines}#conclusion"]),
        "blueprint/roadmap/heaps/withdrawn.md": page(
            "Withdrawn", lean="Pebbles.weigh_le", origin="cited", statement="retracted",
            sources=[f"{lines}#pebbles"]),
    }
