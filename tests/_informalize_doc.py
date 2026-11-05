"""A synthetic output repository for the informalize runtime's tests.

New code, for docs/proposals/informalize.md section 7 (component 2 of section 11): a
two-section document of five items, six parts and one standing hypothesis about an
invented library, `ExampleLib`, written the way the fleet writes one.  `write_repo` lays it
out as `cf informalize init`, the items approval and the conventions and draft phases would:
the runtime copied from templates/informalize/runtime/, the project templates rendered with
`cflib.templates.render`, and the project's own files below.  One item crosses a page break
and one part ends with a display, the two cases the box fitting handles separately.  The
lines the pilot's boxes got wrong are here too: a first line taller than a baselineskip
(a nested fraction in the head line of 1.1), ink below the last line (an underbrace label
ending 1.1, a nested denominator ending 2.3), a tilde under a superscript at a part
boundary (1.2) and stacked accents in a head line (2.3).  The proof sheets must find no
ink under a stroke and none outside its item's boxes.  `CONVENTIONS_NOTIONS` is three
notions for the conventions, each marked with `\fcnotion` and printing the words alone; it
is not written by default, since it would move the page break the boxes above are tuned on.
`stress_section_2` is section 2 again with tall and deep lines at every item and part
boundary, after prose that slides its page break.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
RUNTIME = SKILL / "templates" / "informalize" / "runtime"
PROJECT = SKILL / "templates" / "informalize" / "project"
if str(SKILL / "scripts") not in sys.path:
    sys.path.insert(0, str(SKILL / "scripts"))

from cflib.templates import render  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
#: the commit the fake library's lake-manifest.json pins Mathlib at
MATHLIB_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
JOB = "example-thm"

#: what `cf informalize init` would fill the project templates' slots with
SLOTS = {
    "title_tex": "The glue theorem, after ExampleLib",
    "author_tex": "A. Maintainer",
    "license_tex": "the Apache License, Version~2.0",
    "theorem_tex": r"\texttt{ExampleLib.glue\_theorem}",
    "library_name_tex": "ExampleLib",
    "library_url": "https://example.org/lean-libs/ExampleLib",
    "library_commit": COMMIT,
    "reference_bibitem_tex": r"\bibitem{reference} A. Author, \emph{Gluing}, Example Press, 2001.",
    "title": "The glue theorem, after ExampleLib",
    "theorem": "ExampleLib.glue_theorem",
    "library_name": "ExampleLib",
    "license_name": "the Apache License, Version 2.0",
    "reference_rules": "The text cites A. Author, Gluing, Example Press, 2001, by number only, "
                       "through `\\fcref`.",
}

TEX_PACKAGES = ["amsmath.sty", "amssymb.sty", "amsthm.sty", "keyval.sty", "xspace.sty",
                "zref-savepos.sty", "zref-abspage.sty", "lmodern.sty", "t1lmr.fd",
                "geometry.sty", "xcolor.sty", "hyperref.sty"]

CONFIG = {
    "schema": "formal-companion/informalize-runtime", "schema_version": 1,
    "id": JOB, "job": JOB, "main": "main.tex",
    "library": {"url": SLOTS["library_url"], "commit": COMMIT,
                "module": "ExampleLib.Topology.Glue.Basic", "theorem": "ExampleLib.glue_theorem",
                "prefix": "ExampleLib"},
    "source_date_epoch": 1700000000,
    "boxes": {"res_dpi": 288, "ink_level": 224, "pad_bp": 3.0, "frame_bp": 4.0},
    "tex_packages": TEX_PACKAGES,
}


def _decl(name: str, kind: str, line: int, signature: str) -> dict:
    module = "ExampleLib.Topology.Glue.Basic"
    path = module.replace(".", "/") + ".lean"
    return {"name": name, "module": module, "path": path,
            "line": line, "start_line": line - 2, "end_line": line + 6, "kind": kind,
            "signature": signature,
            "source_url": f"{SLOTS['library_url']}/blob/{COMMIT}/{path}#L{line}"}


DECLARATIONS = {"declarations": sorted([
    _decl("ExampleLib.GlueData", "structure", 12,
          "ExampleLib.GlueData (X : Type u_1) [TopologicalSpace X] : Type u_1"),
    _decl("ExampleLib.glue_map", "def", 30,
          "{X : Type u_1} → [inst : TopologicalSpace X] → Set X → Set X → ExampleLib.Glued X"),
    # a hypothesis nothing depends on prints as the antecedent of an arrow
    _decl("ExampleLib.glue_theorem", "theorem", 40,
          "∀ {X : Type u_1} [inst : TopologicalSpace X] (U V : Set X), IsOpen U → IsOpen V → "
          "ExampleLib.IsPushout U V"),
    # and a dependent one as a binder
    _decl("ExampleLib.glue_surjective", "theorem", 52,
          "∀ {X : Type u_1} [inst : TopologicalSpace X] (U V : Set X) (hU : IsOpen U) "
          "(hV : IsOpen V), Function.Surjective (ExampleLib.glue_map U V)"),
    _decl("ExampleLib.cover_mono", "theorem", 60,
          "∀ {X : Type u_1} {U V W : Set X}, U ⊆ V → ExampleLib.Covers U W → "
          "ExampleLib.Covers V W"),
    _decl("ExampleLib.cover_univ", "theorem", 66,
          "∀ {X : Type u_1} (U : Set X), ExampleLib.Covers Set.univ U"),
    _decl("ExampleLib.glue_card", "theorem", 72,
          "∀ {X : Type u_1} [inst : TopologicalSpace X] {U V : Set X}, IsOpen U → IsOpen V → "
          "ExampleLib.pieces (U ∪ V) ≤ ExampleLib.pieces U + ExampleLib.pieces V"),
    _decl("ExampleLib.chain_long", "theorem", 80,
          "∀ (n : ℕ), ExampleLib.chain n"),
    _decl("ExampleLib.chain_short", "theorem", 90,
          "∀ (n : ℕ), ExampleLib.chain (n + 1) → ExampleLib.chain n"),
], key=lambda d: d["name"]),
    # what the conventions link to: the constants from outside ExampleLib its statements
    # name, as `cf informalize scope` writes them (section 5.3)
    "references": [
        {"name": "IsOpen", "module": "Mathlib.Topology.Defs.Basic", "kind": "def",
         "doc_url": "https://leanprover-community.github.io/mathlib4_docs/"
                    "Mathlib/Topology/Defs/Basic.html#IsOpen",
         "package": "mathlib", "repo": "github.com/leanprover-community/mathlib4",
         "commit": MATHLIB_COMMIT},
        {"name": "Set", "module": "Mathlib.Data.Set.Defs", "kind": "inductive",
         "doc_url": "https://leanprover-community.github.io/mathlib4_docs/"
                    "Mathlib/Data/Set/Defs.html#Set",
         "package": "mathlib", "repo": "github.com/leanprover-community/mathlib4",
         "commit": MATHLIB_COMMIT},
    ]}


def _key(name: str, part: str | None, hypotheses: list[str] | None = None) -> dict:
    module = "ExampleLib.Topology.Glue.Basic"
    line = next(d["line"] for d in DECLARATIONS["declarations"] if d["name"] == name)
    return {"name": name, "module": module, "line": line, "part": part, "role": "key",
            "relation": "exactMatch", "junk": [], "hypotheses": hypotheses or []}


def _parts(*letters: str) -> list[dict]:
    return [{"letter": c, "gist": f"Part ({c}).", "reference": None} for c in letters]


ITEMS = {
    "schema": "formal-companion/informalize-items", "schema_version": 1,
    "library": {"commit": COMMIT, "declarations_sha256": "0" * 64},
    "reference_work": "A. Author, Gluing, Example Press, 2001",
    "sections": [{"number": "1", "title": "Gluing spaces", "slug": "gluing-spaces"},
                 {"number": "2", "title": "Covers", "slug": "covers"}],
    "items": [
        {"id": "1.1", "section": "1", "kind": "definition", "title": "Gluing data",
         "gist": "Gluing data is two open sets that cover a space.", "parts": [], "in_v1": True,
         "reference": {"number": "Definition 2.3", "source": "docstring",
                       "declaration": "ExampleLib.GlueData"},
         "departures": "", "declarations": [_key("ExampleLib.GlueData", None)]},
        {"id": "1.2", "section": "1", "kind": "theorem", "title": "The glue theorem",
         "gist": "(a) The square is a pushout; (b) the induced map is surjective.",
         "parts": _parts("a", "b"), "in_v1": True,
         "reference": {"number": "Theorem 4.1", "source": "inferred", "declaration": None},
         "departures": "",
         "declarations": [
             _key("ExampleLib.glue_theorem", "a", ["hU : IsOpen U", "hV : IsOpen V"]),
             _key("ExampleLib.glue_surjective", "b", ["hU : IsOpen U", "hV : IsOpen V"]),
             {**_key("ExampleLib.glue_map", None), "role": "helper",
              "relation": "relatedMatch"}]},
        {"id": "2.1", "section": "2", "kind": "lemma", "title": "Monotonicity of covers",
         "gist": "(a) A larger set covers what a smaller one covers; (b) the space covers all.",
         "parts": _parts("a", "b"), "in_v1": True, "reference": None, "departures": "",
         "declarations": [_key("ExampleLib.cover_mono", "a"),
                          _key("ExampleLib.cover_univ", "b")]},
        {"id": "2.2", "section": "2", "kind": "example", "title": "A long chain",
         "gist": "(a) Every chain is long; (b) a longer chain gives a shorter one.",
         "parts": _parts("a", "b"), "in_v1": True, "reference": None, "departures": "",
         "declarations": [_key("ExampleLib.chain_long", "a"),
                          _key("ExampleLib.chain_short", "b")]},
        {"id": "2.3", "section": "2", "kind": "corollary", "title": "Counting glued pieces",
         "gist": "Gluing two open sets adds at most their pieces.", "parts": [], "in_v1": True,
         "reference": {"number": "Corollary 4.7", "source": "module docstring",
                       "declaration": "ExampleLib.glue_card"},
         "departures": "",
         "declarations": [_key("ExampleLib.glue_card", None, ["hU : IsOpen U"])]},
        {"id": "2.4", "section": "2", "kind": "remark", "title": "An item left for later",
         "gist": "Not in version 1.", "parts": [], "in_v1": False, "reference": None,
         "departures": "", "declarations": []},
    ],
    "deferred": [],
    "decisions": [],
}

HYPOTHESES = {"schema": "formal-companion/informalize-hypotheses", "schema_version": 1,
              "hypotheses": [{"key": "open", "macro": "\\hypOpen", "binder_head": "IsOpen"}]}

NOTATION_HYPOTHESES = r"""
\fcdefhyp{\hypOpen}{open}{$U$ and $V$ are open}
"""

#: the notions the conventions fix, each marked with the constant it is and printing the
#: words alone: two of Mathlib (`references` rows) and one of the library itself
CONVENTIONS_NOTIONS = r"""
A \fcnotion{Set}{subset} of $X$ is a set of points of $X$, and it is
\fcnotion{IsOpen}{open} when it is a neighbourhood of each of its points.
\fcnotion{ExampleLib.GlueData}{Gluing data} on $X$ is a pair of subsets whose
union is $X$.
"""

SECTIONS_TEX = r"""\fcinputsection{1}{01-gluing-spaces}{Gluing spaces}
\fcinputsection{2}{02-covers}{Covers}
"""

SECTION_1 = r"""\begin{definition}[id=1.1]{Gluing data}
\fcref{Definition 2.3}
Gluing data on $X$, written $\dfrac{\dfrac{U}{V}}{X}$, is two open subsets of $X$
with $\underbrace{U \cup V}_{\text{union}} = X$.
\leanref{ExampleLib.GlueData}
\end{definition}

\begin{theorem}[id=1.2]{The glue theorem}
\fcref*{Theorem 4.1}
Let $U$ and $V$ be subsets of $X$ whose union is $X$, and suppose that \hypOpen.
\itempart{a}{%
  The square formed by $U \cap V$, $U$, $V$ and $X$ is a pushout over $g_{y_{j}}$.
  \leanref{ExampleLib.glue_theorem}}
\itempart{b}{%
  For $\tilde{k}^{\tilde{k}}_{\tilde{k}}$ pieces, the map induced by gluing is surjective.
  \leanref{ExampleLib.glue_surjective}\leanref{ExampleLib.glue_map}}
\end{theorem}
"""

#: enough lines that example 2.2 crosses from the first page to the second
_FILLER = " ".join(["The chain $c_n$ continues one step further, and each step is a link"
                    " of the chain."] * 8)

SECTION_2 = r"""
\begin{lemma}[id=2.1]{Monotonicity of covers}
Let $U$, $V$ and $W$ be subsets of $X$.
\itempart{a}{%
  If $U \subseteq V$ and $U$ covers $W$, then $V$ covers $W$.
  \leanref{ExampleLib.cover_mono}}
\itempart{b}{%
  The whole space $X$ covers $U$.
  \leanref{ExampleLib.cover_univ}}
\end{lemma}

\begin{example}[id=2.2]{A long chain}
Let $n$ be a natural number.
\itempart{a}{%
  FILLER FILLER FILLER FILLER
  The chain $c_n$ is long.
  \leanref{ExampleLib.chain_long}}
\itempart{b}{%
  If the chain $c_{n+1}$ is long, then so is $c_n$:
  \[
    c_{n+1} \Longrightarrow c_n.
  \]
  \leanref{ExampleLib.chain_short}}
\end{example}

\begin{corollary}[id=2.3]{Counting glued pieces}
\fcref{Corollary 4.7}
Let $U$ and $V$ be subsets of $X$ with hull $\hat{\hat{\hat{U}}}$, and suppose that
\hypOpen.  By \itemref[a]{1.2}, the number of pieces satisfies
\[
  n(U \cup V) \le n(U) + n(V),
\]
with $\dfrac{1}{\dfrac{2}{3}}$ to spare.
\leanref{ExampleLib.glue_card}
\end{corollary}
""".replace("FILLER", _FILLER)


_STRESS = r"""
FILLER

\begin{lemma}[id=2.1]{Monotonicity of covers}
Let $U$, $V$ and $W$ be subsets of $X$ with $\dfrac{\dfrac{a}{b}}{\dfrac{c}{d}}$ and
$\underbrace{U \cap V}_{\text{meet}}$.
\itempart{a}{%
  $\overbrace{U \subseteq V}^{\text{inclusion}}$ and $U$ covers $W$, then $V$ covers
  $\underbrace{W}_{\text{target}}$.
  \leanref{ExampleLib.cover_mono}}
\itempart{b}{%
  $\dfrac{\dfrac{1}{2}}{3}$: the whole space $X$ covers $U$, and
  \begin{itemize}
  \item $\tilde{k}^{\tilde{k}}$ is one case;
  \item $\dfrac{a}{\dfrac{b}{c}}$ is another.
  \end{itemize}
  \leanref{ExampleLib.cover_univ}}
\end{lemma}

\begin{example}[id=2.2]{A long chain}
$\overbrace{n}^{\text{number}}$ is a natural number, $\dfrac{\dfrac{n}{2}}{3}$.
\itempart{a}{%
  The chain $\underbrace{c_n}_{\text{chain}}$ is long, and $\dfrac{1}{\dfrac{2}{c_n}}$.
  \leanref{ExampleLib.chain_long}}
\itempart{b}{%
  $\overbrace{c_{n+1}}^{\text{next}}$ long gives $c_n$ long:
  \[
    \dfrac{c_{n+1}}{\dfrac{a}{b}} \Longrightarrow \underbrace{c_n}_{\text{shorter}}.
  \]
  \leanref{ExampleLib.chain_short}}
\end{example}

\begin{corollary}[id=2.3]{Counting glued pieces}
\fcref{Corollary 4.7}
$\dfrac{\dfrac{U}{V}}{X}$: let $U$ and $V$ be subsets of $X$ with hull
$\hat{\hat{\hat{U}}}$, and suppose that \hypOpen.  By \itemref[a]{1.2}, the number of
pieces satisfies
\[
  n(U \cup V) \le n(U) + n(V),
\]
with $\dfrac{1}{\dfrac{2}{3}}$ to spare and $\underbrace{n(U)}_{\text{pieces}}$.
\leanref{ExampleLib.glue_card}
\end{corollary}
"""


def stress_section_2(n: int) -> str:
    """Section 2 with a fraction, a brace or a list on both sides of every item and part
    boundary, where \\lineskip leaves the lines 2pt apart, after `n` sentences of prose."""
    sentence = ("The chain $c_n$ continues one step further, and each step is a link of the "
                "chain.")
    return _STRESS.replace("FILLER", " ".join([sentence] * n))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def write_repo(root: Path, *, sections: dict[str, str] | None = None,
               items: dict | None = None, hypotheses: dict | None = None,
               notation_extra: str = NOTATION_HYPOTHESES, config: dict | None = None,
               conventions_extra: str = "", declarations: dict | None = None) -> Path:
    """Lay out an output repository under ``root``; returns ``root``.

    The keyword arguments replace one file's content: ``sections`` maps a section file's
    name (``01-gluing-spaces``) to its text.
    """
    informal = root / "informal"
    # as `cf informalize sync` copies it: the runtime's files, never bytecode
    shutil.copytree(RUNTIME, informal, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for template in sorted(p for p in PROJECT.rglob("*") if p.is_file()):
        rel = template.relative_to(PROJECT)
        text = render(template.read_text(encoding="utf-8"), SLOTS)
        if rel.as_posix() == "gitignore":
            dest = root / ".gitignore"
        elif rel.as_posix() == "README.md":
            dest = root / "README.md"
        else:
            dest = informal / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    notation = informal / "notation.sty"
    text = notation.read_text(encoding="utf-8")
    marker = "% Standing hypotheses (one \\fcdefhyp per key of hypotheses.json).\n"
    notation.write_text(text.replace(marker, marker + notation_extra), encoding="utf-8")
    conventions = informal / "sections" / "00-conventions.tex"
    conventions.write_text(conventions.read_text(encoding="utf-8") + conventions_extra,
                           encoding="utf-8")
    write_json(informal / "informalize.json", config or CONFIG)
    write_json(informal / "declarations.json", declarations or DECLARATIONS)
    write_json(informal / "items.json", items or ITEMS)
    write_json(informal / "hypotheses.json", hypotheses or HYPOTHESES)
    (informal / "sections.tex").write_text(SECTIONS_TEX, encoding="utf-8")
    files = {"01-gluing-spaces": SECTION_1, "02-covers": SECTION_2}
    files.update(sections or {})
    for name, body in files.items():
        (informal / "sections" / f"{name}.tex").write_text(body, encoding="utf-8")
    return root
