"""templates/informalize/: the LaTeX runtime and the project templates of the informalize mode.

New code, for docs/proposals/informalize.md section 7 (component 2 of section 11).  Three
groups:

* the files themselves: what section 7.1 lists is there, the Python tools import nothing
  but the standard library and their siblings and write no bytecode, the shell parses, the
  packages the marker package and the main template load are the ones informalize.json
  lists, the project templates' slots are the ones their README names, and nothing of the
  pilot's own text is left;
* the checks of `check_items.py` (rules 1 to 7) and the parsing they rest on, run on a
  fake build's .aux, so they need no TeX;
* the whole runtime on the synthetic document of `tests/_informalize_doc.py`: two clean
  builds that are byte-identical, the boxes, the checks, the proof sheets, and each LaTeX
  error and check failure caught.  These need pdflatex, latexmk, kpsewhich and poppler and
  skip without them; CF_REQUIRE_TEX=1 (what CI's TeX job sets) makes the skip a failure.
"""
from __future__ import annotations

# The first import, before anything reads HOME: it pins HOME at a temporary
# directory for `unittest discover`, which loads no conftest.py.  The second
# spelling is for `python3 -m unittest tests.<module>`.  See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import argparse
import ast
import copy
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _informalize_doc as doc                                # noqa: E402

RUNTIME = doc.RUNTIME
PROJECT = doc.PROJECT
TOOLS = RUNTIME / "tools"
SPEC = (SKILL / "docs" / "proposals" / "informalize.md").read_text(encoding="utf-8")


def load_tool(name: str):
    """Import a runtime tool by path, as `python3 informal/tools/<tool>.py` would.

    The tool sets `sys.dont_write_bytecode` itself, but only once it runs, after the import
    has compiled it; so the flag is set here first, or the import would leave a
    `__pycache__/` in templates/informalize/runtime/tools/ for every copy of it to carry.
    """
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:                  # pragma: no cover
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


fc = load_tool("fccommon")
check_items = load_tool("check_items")
extract_markers = load_tool("extract_markers")
proof_sheets = load_tool("proof_sheets")

TEX_PROGRAMS = ("pdflatex", "latexmk", "kpsewhich")
POPPLER = ("pdftotext", "pdftoppm", "pdfinfo")


def spec_runtime_example() -> dict:
    for body in re.findall(r"^```json\n(.*?)^```", SPEC, re.M | re.S):
        data = json.loads(body)
        if data["schema"] == fc.RUNTIME_SCHEMA:
            return data
    raise AssertionError("the specification has no informalize.json example")


# ---------------------------------------------------------------------------
# the files

class TestTheRuntimeFiles(unittest.TestCase):

    def test_section_7_1_lists_what_is_there(self):
        table = SPEC[SPEC.index("| runtime file |"):SPEC.index("The tools are standard-library")]
        listed = set(re.findall(r"^\| `([^`]+)` \|", table, re.M))
        shipped = {p.relative_to(RUNTIME).as_posix() for p in RUNTIME.rglob("*") if p.is_file()
                   and "__pycache__" not in p.parts}
        self.assertEqual(listed - shipped, set())
        self.assertEqual(shipped - listed, set())

    def test_the_runtime_carries_its_license(self):
        # Apache-2.0 section 4(a): a copy of the runtime in an output repository, whose own
        # LICENSE is the text's, carries the license text with it
        self.assertEqual((RUNTIME / "LICENSE-runtime").read_bytes(),
                         (SKILL / "LICENSE").read_bytes())
        self.assertIn("LICENSE-runtime", (RUNTIME / "STYLE.md").read_text(encoding="utf-8"))

    def test_the_python_tools_import_only_the_standard_library_and_their_siblings(self):
        siblings = {p.stem for p in TOOLS.glob("*.py")}
        allowed = set(sys.stdlib_module_names) | {"__future__"} | siblings
        for path in sorted(TOOLS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            with self.subTest(tool=path.name):
                names = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names |= {a.name.split(".")[0] for a in node.names}
                    elif isinstance(node, ast.ImportFrom):
                        names.add((node.module or "").split(".")[0])
                self.assertLessEqual(names, allowed)
                self.assertIsNotNone(ast.get_docstring(tree))
                # no bytecode: the flag is set before the first sibling is imported
                text = path.read_text(encoding="utf-8")
                flag = text.index("sys.dont_write_bytecode = True")
                for sibling in siblings - {path.stem}:
                    for m in re.finditer(rf"^\s*import {sibling}\b", text, re.M):
                        self.assertLess(flag, m.start())

    def test_the_runtime_holds_no_bytecode(self):
        # `cf informalize sync` copies every file under runtime/, so a __pycache__ there
        # would land in every output repository
        self.assertEqual([p for p in RUNTIME.rglob("*")
                          if p.name == "__pycache__" or p.suffix == ".pyc"], [])

    def test_the_runtime_s_vocabulary_is_the_specification_s(self):
        flat = " ".join(SPEC.split())
        kinds = re.search(r"`kind` is one of ([a-z, ]+)\.", flat).group(1).split(", ")
        roles = re.findall(r"`([a-z]+)`", re.search(r"`role` is (`[a-z]+` or `[a-z]+`)",
                                                     flat).group(1))
        sources = re.findall(r"`([a-z ]+)`", re.search(r"`source` one of ((?:`[a-z ]+`,? )+)",
                                                       flat).group(1))
        self.assertEqual(list(fc.KIND_NAMES), kinds)
        self.assertEqual(list(fc.ROLES), roles)
        self.assertEqual(list(fc.REFERENCE_SOURCES), sources)
        # and cflib's, of which these are the documented copies (CLAUDE.md section 4)
        from cflib import vocab
        for name, mine in (("INFORMALIZE_KINDS", tuple(fc.KIND_NAMES)),
                           ("INFORMALIZE_ROLES", fc.ROLES),
                           ("INFORMALIZE_RELATIONS", fc.RELATIONS),
                           ("REFERENCE_SOURCES", fc.REFERENCE_SOURCES)):
            with self.subTest(name=name):
                self.assertEqual(tuple(getattr(vocab, name)), tuple(mine))
        self.assertIn("`relation` is a Literature Links relation: "
                      + ", ".join(f"`{r}`" for r in fc.RELATIONS[:-1])
                      + f" or `{fc.RELATIONS[-1]}`", flat)

    def test_the_item_rules_are_one_copy(self):
        """tools/fcitems.py holds the item table's rules for the gate and for `check-items`
        (section 8.3 rule 4): the skill imports them, and its name patterns are copies of
        informalize.config's."""
        sys.path.insert(0, str(SKILL / "scripts"))
        try:
            from informalize import config, itemtable, runtime
        finally:
            sys.path.remove(str(SKILL / "scripts"))
        rules = runtime.fcitems()
        self.assertEqual(Path(rules.__file__).resolve(), (TOOLS / "fcitems.py").resolve())
        for name in ("items_problems", "declarations_problems", "keyword_problem",
                     "check_table", "SourceTree", "KEYWORDS"):
            with self.subTest(name=name):
                self.assertIs(getattr(itemtable, name), getattr(rules, name))
        for name in ("COMMIT_PATTERN", "MODULE_PATTERN", "DECLARATION_PATTERN"):
            with self.subTest(pattern=name):
                self.assertEqual(getattr(rules, name), getattr(config, name))
        # its vocabulary is fccommon's, the one copy the test above pins
        self.assertEqual(Path(rules.fc.__file__).resolve(),
                         (TOOLS / "fccommon.py").resolve())

    def test_an_item_id_is_two_numbers_from_1(self):
        # the grammar cflib holds the table to (`approve`, `check-items`) is the one copy
        # the runtime keeps, character for character and on every case
        from cflib import items
        self.assertEqual(fc.ITEM_ID.pattern, items.INFORMAL_ITEM_ID.pattern)
        for good in ("1.1", "2.10", "12.3"):
            self.assertRegex(good, fc.ITEM_ID)
            self.assertTrue(items.is_informal_item_id(good))
        for bad in ("0.1", "1.0", "01.2", "1.02", "1.2a", "1.2(a)", " 1.2", "1", "1.2\n"):
            with self.subTest(id=bad):
                self.assertIsNone(fc.ITEM_ID.match(bad))
                self.assertFalse(items.is_informal_item_id(bad))

    def test_the_shell_parses(self):
        for shell in ("sh", "bash"):
            if shutil.which(shell) is None:                  # pragma: no cover
                continue
            with self.subTest(shell=shell):
                out = subprocess.run([shell, "-n", str(TOOLS / "build_pdf.sh")],
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_packages_loaded_are_the_ones_informalize_json_lists(self):
        listed = set(spec_runtime_example()["tex_packages"])
        self.assertEqual(listed, set(doc.TEX_PACKAGES))
        sty = (RUNTIME / "fcmarkers.sty").read_text(encoding="utf-8")
        main = (PROJECT / "main.tex").read_text(encoding="utf-8")
        notation = (PROJECT / "notation.sty").read_text(encoding="utf-8")
        loaded = set()
        for text in (sty, main, notation):
            for group in re.findall(r"\\(?:RequirePackage|usepackage)(?:\[[^]]*\])?\{([^}]*)\}",
                                    text):
                loaded |= {f"{name.strip()}.sty" for name in group.split(",")}
        own = {"fcmarkers.sty", "notation.sty", "fontenc.sty"}     # fontenc is LaTeX's own
        self.assertLessEqual(loaded - own, listed)
        self.assertIn("t1lmr.fd", listed)                     # T1 with lmodern
        self.assertIn("\\usepackage[T1]{fontenc}", main)

    def test_a_hypothesis_macro_keeps_the_space_after_its_words(self):
        """TeX drops the space after a control word and `\\@ifstar` drops one before the
        token it looks at, so `\\hypOpen and` printed "openand"; the words end with
        `\\xspace`.  The built document is checked in TestPartialBuilds."""
        sty = (RUNTIME / "fcmarkers.sty").read_text(encoding="utf-8")
        (line,) = [ln for ln in sty.splitlines() if "\\DeclareRobustCommand#1" in ln]
        self.assertIn("\\@ifstar{#3\\xspace}{\\fc@hyp{#2}#3\\xspace}", line)
        self.assertIn("\\RequirePackage{xspace}", sty)

    @staticmethod
    def _documented(heading: str) -> set[str]:
        """The slots one section of templates/informalize/README.md names."""
        readme = (SKILL / "templates" / "informalize" / "README.md").read_text(encoding="utf-8")
        section = readme.split(f"\n## {heading}", 1)[1].split("\n## ", 1)[0]
        return set(re.findall(r"^\| `([a-z_]+)` \|", section, re.M))

    @staticmethod
    def _used(*paths: Path) -> set[str]:
        out: set[str] = set()
        for root in paths:
            for path in ([root] if root.is_file() else root.rglob("*")):
                if path.is_file():
                    out |= set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}",
                                          path.read_text(encoding="utf-8")))
        return out

    def test_the_project_slots_are_the_readme_s(self):
        used = self._used(PROJECT)
        self.assertEqual(used, self._documented("`project/`"))
        self.assertEqual(used, set(doc.SLOTS))

    def test_the_landing_pages_slots_are_the_readme_s(self):
        """`templates/informalize/site/` is rendered by `cf informalize overlay`, not by
        `init`, and its slots are documented in their own section (section 10)."""
        index = SKILL / "templates" / "informalize" / "site" / "index.html"
        self.assertEqual(self._used(index), self._documented("`site/`"))

    def test_nothing_of_the_pilot_s_own_text_is_left(self):
        # the provenance lines name the pilot's files ("ported from the pilot's
        # rrmarkers.sty"), as CLAUDE.md section 3 asks; nothing else of it is left
        pilot = ("rr@", "rr:", "RROnly", "stich", "hypF", "hypEx", "Riemann", "Roch",
                 "TauCeti", "decls-", "divisor")
        for root in (RUNTIME, PROJECT):
            for path in sorted(p for p in root.rglob("*") if p.is_file()
                               and "__pycache__" not in p.parts and p.suffix != ".pyc"):
                text = path.read_text(encoding="utf-8")
                for word in pilot:
                    with self.subTest(file=path.name, word=word):
                        self.assertFalse(word in text, f"{path.name} says {word!r}")

    def test_the_spec_example_config_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc.write_json(Path(tmp) / "informalize.json", spec_runtime_example())
            config = fc.load_config(Path(tmp))
        self.assertEqual(config.boxes, {"res_dpi": 288, "ink_level": 224, "pad_bp": 3.0,
                                        "frame_bp": 4.0})
        self.assertIsNone(config.source_date_epoch)       # filled by I1; the build refuses null

    def test_a_bad_config_is_refused(self):
        breaks = {
            "a job with a slash": {"job": "../x"},
            "a main file in a directory": {"main": "sub/main.tex"},
            "an unknown box setting": {"boxes": {"res_dpi": 288, "dpi": 3}},
            "a negative time": {"source_date_epoch": -1},
            "a package with a space": {"tex_packages": ["a b.sty"]},
            "another schema": {"schema": "formal-companion/informalize-items"},
        }
        for what, change in breaks.items():
            with self.subTest(broken=what), tempfile.TemporaryDirectory() as tmp:
                doc.write_json(Path(tmp) / "informalize.json", {**doc.CONFIG, **change})
                with self.assertRaises(fc.ToolError):
                    fc.load_config(Path(tmp))


# ---------------------------------------------------------------------------
# parsing, and rules 1 to 7 on a fake build

def fake_aux(items: dict = doc.ITEMS, decls: dict = doc.DECLARATIONS) -> str:
    """The .aux a correct build of ``items`` would write (records only, no positions)."""
    signatures = {d["name"]: d["signature"] for d in decls["declarations"]}
    lines = ["\\fc@auxhypdef{open}{\\hypOpen}"]
    by_section: dict[str, list[dict]] = {}
    for item in items["items"]:
        if item["in_v1"]:
            by_section.setdefault(item["section"], []).append(item)
    for sec in items["sections"]:
        n = sec["number"]
        lines.append(f"\\fc@auxsection{{{n}}}{{1}}{{{int(n):02d}-{sec['slug']}}}{{{sec['title']}}}")
        for item in by_section.get(n, []):
            iid = item["id"]
            lines.append(f"\\fc@auxitem{{{iid}}}{{{item['kind']}}}{{{n}}}{{{item['title']}}}")
            ref = item.get("reference")
            if ref and ref.get("number"):
                star = "*" if ref["source"] == "inferred" else ""
                lines.append(f"\\fc@auxref{{{iid}}}{{}}{{{ref['number']}}}{{{star}}}")
            keys = [d for d in item["declarations"] if d["role"] == "key"]
            if any("IsOpen" in signatures[d["name"]] for d in keys):
                lines.append(f"\\fc@auxhyp{{{iid}}}{{}}{{open}}")
            for d in keys:
                if d["part"] is None:
                    lines.append(f"\\fc@auxleanref{{{iid}}}{{}}{{{d['name']}}}")
            for part in item["parts"]:
                c = part["letter"]
                lines.append(f"\\fc@auxpart{{{iid}}}{{{c}}}")
                for d in keys:
                    if d["part"] == c:
                        lines.append(f"\\fc@auxleanref{{{iid}}}{{{c}}}{{{d['name']}}}")
    return "\n".join(lines) + "\n"


def run_rules(aux_text: str, items: dict = doc.ITEMS, hyps: dict = doc.HYPOTHESES,
              log: str = "", decls: dict = doc.DECLARATIONS) -> check_items.Report:
    """Rules 1 to 7 on an .aux text, as `check_items.py --aux-only` runs them."""
    with tempfile.TemporaryDirectory() as tmp:
        doc.write_json(Path(tmp) / "items.json", items)
        doc.write_json(Path(tmp) / "hypotheses.json", hyps)
        loaded = fc.load_items(Path(tmp) / "items.json")
        rows, problems = fc.load_hypotheses(Path(tmp) / "hypotheses.json")
    decls = {d["name"]: d for d in decls["declarations"]}
    aux = fc.parse_aux(aux_text)
    report = check_items.Report()
    check_items.check_log(log, aux, report)
    check_items.check_hypdefs(aux, rows, problems, report)
    args = argparse.Namespace(items=None, section=None)
    check_items.check_aux(aux, loaded, decls, rows, check_items.scope_of(args, aux, loaded), report)
    return report


def rules_of(report: check_items.Report, warnings: bool = False) -> set[int]:
    lines = report.fails + (report.warns if warnings else [])
    return {int(m.group(1)) for line in lines for m in [re.search(r"\[(\d+)\]", line)] if m}


class TestParsing(unittest.TestCase):

    def test_the_aux_records_are_read(self):
        aux = fc.parse_aux(fake_aux() + "\\zref@newlabel{fc:1.1:start}{\\posx{10}\\posy{20}"
                           "\\abspage{1}}\n\\fc@auxgeometry{textwidth=5,texttop=7}\n")
        self.assertEqual([s["number"] for s in aux.sections], ["1", "2"])
        self.assertEqual([i["id"] for i in aux.items], ["1.1", "1.2", "2.1", "2.2", "2.3"])
        self.assertEqual(aux.parts_of("1.2"), ["a", "b"])
        self.assertEqual(aux.decls_of("1.2", "b"), ["ExampleLib.glue_surjective"])
        self.assertEqual(aux.refs[1], {"id": "1.2", "part": "", "number": "Theorem 4.1",
                                       "star": True})
        self.assertEqual(aux.hypdefs, [{"key": "open", "macro": "\\hypOpen"}])
        self.assertEqual(aux.positions["fc:1.1:start"], {"posx": 10, "posy": 20, "abspage": 1})
        self.assertEqual(aux.geometry, {"textwidth": 5, "texttop": 7})

    def test_a_notion_is_read_with_its_constant_and_its_words(self):
        aux = fc.parse_aux("\\fc@auxnotion{1}{Metric.ball}{the open ball $\\ball {x}{r}$}\n"
                           "\\fc@auxnotion{2}{ExampleLib.GlueData}{gluing data}\n")
        self.assertEqual(aux.notions, [
            {"index": "1", "name": "Metric.ball", "words": "the open ball $\\ball{x}{r}$"},
            {"index": "2", "name": "ExampleLib.GlueData", "words": "gluing data"}])

    def test_notion_titles_preserve_math_and_leave_printed_words_separate(self):
        aux = fc.parse_aux(r"\fc@auxnotion{1}{ExampleLib.map}{$\iota_S$}" + "\n" +
                           r"\fc@auxnotiontitle{1}{Inclusion $\iota _S$}")
        self.assertEqual(aux.notions[0]["title"], r"Inclusion $\iota_S$")
        self.assertEqual(aux.notions[0]["words"], r"$\iota_S$")
        entry = extract_markers.entry_skeletons(aux)[0]
        self.assertEqual(entry["title"], r"Inclusion $\iota_S$")
        source = r"\fcnotion[Subpath $\gamma_{[r,s]}$]{ExampleLib.map}{$\gamma$}"
        self.assertEqual([n for _, _, n in fc.notion_calls(source)], ["ExampleLib.map"])
        self.assertEqual(fc.printed_lean_names(source, ["ExampleLib.map"]), [])
        with self.assertRaises(ValueError):
            fc.parse_aux(r"\fc@auxnotiontitle{99}{Missing}")

    def test_a_title_is_read_in_the_spelling_a_person_types(self):
        # TeX's \write puts a space after every control word
        self.assertEqual(fc.tex_text("The map $\\pi _1(X)$ and \\emph {it}"),
                         "The map $\\pi_1(X)$ and \\emph{it}")
        self.assertEqual(fc.tex_text("\\alpha x\n  y"), "\\alpha x y")
        aux = fc.parse_aux("\\fc@auxitem{1.1}{lemma}{1}{^^c3^^a9t\\'e $\\pi _1$}\n")
        self.assertEqual(aux.items[0]["title"], "ét\\'e $\\pi_1$")

    def test_the_names_a_text_file_prints_are_the_ones_it_typesets(self):
        """`printed_lean_names`, the one definition the gate's rule 2 and
        `cf informalize check-items` both ask of a text file."""
        names = {"ExampleLib.glue_theorem", "Path.trans"}
        self.assertEqual(fc.printed_lean_names(
            "One line.\nThe theorem ExampleLib.glue\\_theorem closes it.", names),
            [(2, "ExampleLib.glue_theorem")])
        for silent in (r"\leanref{ExampleLib.glue_theorem}",
                       r"\fcnotion{Path.trans}{$\gamma \ptrans \delta$}",
                       r"% ExampleLib.glue\_theorem, a note to myself",
                       r"\cite{library} \label{eq:1.2-trans} \input{sections}"):
            with self.subTest(silent=silent):
                self.assertEqual(fc.printed_lean_names(silent, names), [])
        # a dotted word that is no name of the library's, and a name with no namespace
        self.assertEqual(fc.printed_lean_names("A path, i.e. a map, is trans.", names), [])

    def test_hypotheses_are_found_as_binders_and_as_arrows(self):
        def heads(signature: str) -> set[str]:
            return {h for h in map(fc.type_head, fc.hypothesis_types(signature)) if h}
        self.assertIn("IsOpen", heads("∀ {X : Type} (U : Set X), IsOpen U → IsClosed U → P U"))
        self.assertIn("IsClosed", heads("∀ {X : Type} (U : Set X), IsOpen U → IsClosed U → P U"))
        self.assertNotIn("P", heads("∀ {X : Type} (U : Set X), IsOpen U → IsClosed U → P U"))
        self.assertIn("IsOpen", heads("∀ (U : Set X) (hU : IsOpen U), P U"))
        self.assertIn("IsOpen", heads("∀ (U : Set X) ⦃h : @IsOpen X _ U⦄, P U"))
        self.assertIn("Finite", heads("∀ (s : Set X) [Finite s], P s"))
        self.assertIn("Finite", heads("∀ (s : Set X) [h : Finite s], P s"))
        # a name is a whole name: IsOpenMap is not IsOpen
        self.assertNotIn("IsOpen", heads("∀ (f : X → Y) (hf : IsOpenMap f), P f"))
        # what a hypothesis's conclusion holds is not a hypothesis
        self.assertNotIn("IsOpen", heads("∀ (U : Set X), P U → IsOpen U"))
        # nor what one side of an equivalence binds, or an existential's body
        self.assertEqual(heads("∀ {X : Type} (W : Set X), ExampleLib.Covers Set.univ W ↔ "
                               "∀ (s : Set X), IsOpen s → s ⊆ W"), {"Type", "Set"})
        self.assertNotIn("IsPathConnected",
                         heads("∀ (x : X), ∃ U, IsOpen U ∧ (IsPathConnected U → x ∈ U)"))
        # nor the inside of a binder's type: f is a function, not a hypothesis IsOpen U
        self.assertNotIn("IsOpen", heads("∀ {X : Type} (f : IsOpen U → Prop), P f"))
        # a def's type prints its binders with arrows
        self.assertEqual(heads("{X : Type u_1} → [inst : TopologicalSpace X] → Set X → "
                               "ExampleLib.Glued X"), {"Type", "TopologicalSpace", "Set"})

    def test_a_hypothesis_is_matched_by_its_head_or_its_printed_type(self):
        types = fc.hypothesis_types("interior A ∪ interior B = Set.univ → IsPathConnected A → "
                                    "IsPathConnected (A ∩ B) → ∀ (h : x ∈ A), P x")
        self.assertEqual(types, ["interior A ∪ interior B = Set.univ", "IsPathConnected A",
                                 "IsPathConnected (A ∩ B)", "x ∈ A"])
        both = {"binder_type": r"IsPathConnected \(A ∩ B\)"}
        self.assertEqual([t for t in types if fc.hypothesis_matches(both, t)],
                         ["IsPathConnected (A ∩ B)"])
        cover = {"binder_type": r"interior \w+ ∪ interior \w+ = Set\.univ"}
        self.assertTrue(fc.hypothesis_matches(cover, types[0]))
        self.assertTrue(fc.hypothesis_matches({"binder_head": "IsPathConnected"}, types[1]))
        self.assertFalse(fc.hypothesis_matches({"binder_head": "IsPath"}, types[1]))

    def test_json_keeps_a_string_that_looks_like_a_list(self):
        data = {"title": "The interval $[ 0, 1 ]$", "boxes": [[1, 0.1, 0.2, 0.3, 0.4]]}
        text = fc.dump_json(data)
        self.assertEqual(json.loads(text), data)
        self.assertIn("\n  [1, 0.1, 0.2, 0.3, 0.4]\n", text)

    def test_light_grey_counts_as_ink(self):
        # a pixel of gray 200, below the ink level 224 the pilot settled on and above 160
        w, h = 40, 40
        ppm = bytearray(b"P6\n%d %d\n255\n" % (w, h) + b"\xff" * (3 * w * h))
        offset = len(b"P6\n%d %d\n255\n" % (w, h))
        i = offset + 3 * (10 * w + 20)
        ppm[i:i + 3] = b"\xc8\xc8\xc8"
        entry = {"id": "1.1", "number": "1.1"}
        box = [1, 0.1, 10 / h, 0.8, 0.5]                       # top edge on row 10
        for level, caught in ((224, True), (160, False)):
            with self.subTest(ink_level=level):
                img = proof_sheets.Image(bytes(ppm), level)
                out: dict = {}
                proof_sheets.check_page(img, [proof_sheets.Box(entry, box, w, h)], [], 1, out,
                                        False, False, 1)
                self.assertEqual("ink under a stroke" in out, caught, out)
                pgm = b"P5\n%d %d\n255\n" % (w, h) + bytes(
                    200 if (y, x) == (10, 20) else 255 for y in range(h) for x in range(w))
                ink = extract_markers.PageInk(pgm, (w, h), level)
                self.assertEqual(ink.rows[10] is not None, caught)

    def test_a_stroke_that_only_touches_ink_is_a_tight_edge(self):
        # ink in the row just above a box's top edge: the 2-pixel stroke covers it, the edge
        # itself does not cross it
        w, h = 40, 40
        ppm = bytearray(b"P6\n%d %d\n255\n" % (w, h) + b"\xff" * (3 * w * h))
        offset = len(b"P6\n%d %d\n255\n" % (w, h))
        ppm[offset + 3 * (9 * w + 20):offset + 3 * (9 * w + 21)] = b"\x00\x00\x00"
        img = proof_sheets.Image(bytes(ppm), 224)
        out: dict = {}
        proof_sheets.check_page(img, [proof_sheets.Box({"id": "1.1", "number": "1.1"},
                                                       [1, 0.1, 10 / h, 0.8, 0.5], w, h)],
                                [], 1, out, False, False, 1)
        self.assertNotIn("ink under a stroke", out)
        self.assertEqual(out["tight edge"],
                         ["p.1 1.1: ink near the top edge (1 ink pixels under the stroke)"])

    def test_items_keep_their_frames_apart_only_with_room_to_spare(self):
        params = extract_markers.Params(288, 224, 3.0, 4.0)

        def pair(gap: float) -> tuple[float, float]:
            a = extract_markers.Span(1, 100, 110, 80, 500, None, None, bottom=110 + gap / 2)
            b = extract_markers.Span(1, 110 + gap, 130, 80, 500, None, None, top=110 + gap / 2)
            extract_markers.separate_items([b, a], params)
            return a.bottom - a.ink_bottom, b.ink_top - b.top
        # room for frame_bp and CLEAR_BP of clearance on each side: frames apart
        self.assertEqual(pair(10.0), (3.0, 3.0))
        self.assertEqual(pair(8.5), (2.25, 2.25))
        # less: one edge halfway, never an edge within a pixel of ink, as frame_bp would put it
        self.assertEqual(pair(5.75), (2.875, 2.875))
        self.assertEqual(pair(3.0), (1.5, 1.5))


#: a synthetic page for the box fitting: 1 pixel per bp, lines 14bp apart with 7 rows of
#: ink each above the baseline, and the text block 100..500bp wide
PAGE_GEOMETRY = {key: int(bp * fc.SP_PER_BP) for key, bp in
                 (("textleft", 100), ("texttop", 100), ("textwidth", 400),
                  ("textheight", 600), ("strutheight", 9), ("strutdepth", 4),
                  ("baselineskip", 14))}
BASELINES = [110 + 14 * n for n in range(20)]


def synthetic_page(extra: tuple[tuple[int, int, int, int], ...] = ()):
    """A `PageInk` of a page of plain lines, read from a PGM as the renderer writes one.

    `extra` adds blots (y0, y1, x0, x1) beside the lines: the ink a tall or deep line
    leaves in the interline gap.
    """
    w, h = 612, 792
    rows = bytearray(b"\xff" * (w * h))
    for baseline in BASELINES:
        for y in range(baseline - 7, baseline):
            rows[y * w + 100:y * w + 500] = b"\x00" * 400
    for y0, y1, x0, x1 in extra:
        for y in range(y0, y1 + 1):
            rows[y * w + x0:y * w + x1] = b"\x00" * (x1 - x0)
    pgm = b"P5\n%d %d\n255\n" % (w, h) + bytes(rows)
    return extract_markers.PageInk(pgm, (float(w), float(h)), 224)


def notion_marker(x: float, baseline: float, page: int = 1) -> dict:
    """A position record, as zref savepos writes one: sp from the lower left corner."""
    return {"posx": int(x * fc.SP_PER_BP), "posy": int((792 - baseline) * fc.SP_PER_BP),
            "abspage": page}


class TestNotionBoxes(unittest.TestCase):
    """`notion_spans`: the words of a `\\fcnotion`, on one line and across a break."""

    def setUp(self):
        self.geo = extract_markers.Geometry(PAGE_GEOMETRY, [(612.0, 792.0)])
        self.ink = [synthetic_page()]
        self.params = extract_markers.Params.of(doc.CONFIG["boxes"])

    def spans(self, start, end, params=None):
        return [s.box() for s in extract_markers.notion_spans(
            self.geo, self.ink, params or self.params, start, end, "notion Test")]

    def test_one_line_is_one_box_between_the_two_markers(self):
        box, = self.spans(notion_marker(200, 138), notion_marker(300, 138))
        page, x, y, w, h = box
        # the left edge is half a pad before the words, the right edge at the last pen
        self.assertEqual((page, round(x, 3), round(x + w, 3)), (1, 198.5, 300.0))
        # the line's ink is rows 131 to 137, padded by 3bp above and below
        self.assertEqual((round(y, 3), round(y + h, 3)), (128.0, 141.0))

    def test_a_pad_that_would_reach_the_line_above_stops_halfway(self):
        wide = extract_markers.Params(res_dpi=72, ink_level=224, pad_bp=6.0, frame_bp=4.0)
        box, = self.spans(notion_marker(200, 138), notion_marker(300, 138), wide)
        # the line above ends at row 124, this one starts at 131: the edge goes between
        self.assertEqual(round(box[2], 3), 127.5)

    def test_a_notion_broken_over_two_lines_gets_a_box_on_each(self):
        first, second = self.spans(notion_marker(400, 138), notion_marker(250, 152))
        self.assertEqual((round(first[1], 3), round(first[1] + first[3], 3)), (398.5, 503.0))
        self.assertEqual((round(second[1], 3), round(second[1] + second[3], 3)), (97.0, 250.0))
        self.assertLess(first[2] + first[4], second[2])

    def test_the_lines_of_a_band_are_its_runs_of_ink(self):
        runs = extract_markers.line_runs(self.ink[0], 125, 152)
        self.assertEqual(runs, [(131, 137), (145, 151)])

    def test_ink_of_the_line_above_in_the_gap_is_not_the_notions_line(self):
        # A nested fraction or an underbrace label on the line above leaves ink in the
        # interline gap: `cut` lands under it, so its leftover run is in the band.  The
        # notion's box is still the one line its markers are on.
        self.ink = [synthetic_page(((125, 128, 300, 340),))]
        box, = self.spans(notion_marker(200, 138), notion_marker(300, 138))
        self.assertEqual((round(box[1], 3), round(box[1] + box[3], 3)), (198.5, 300.0))
        # the leftover ink ends at 129, the words' line starts at 131: the edge goes between
        self.assertEqual((round(box[2], 3), round(box[2] + box[4], 3)), (130.0, 141.0))

    def test_ink_of_the_line_below_in_the_gap_is_not_the_notions_line_either(self):
        self.ink = [synthetic_page(((140, 143, 300, 340),))]
        box, = self.spans(notion_marker(200, 138), notion_marker(300, 138))
        self.assertEqual((round(box[1], 3), round(box[1] + box[3], 3)), (198.5, 300.0))

    def test_a_descender_of_the_notions_own_line_stays_in_its_box(self):
        # (140, 142) is inside the strut of the baseline at 138 (depth 4bp), so it is the
        # notion's own ink and the box grows to hold it.
        self.ink = [synthetic_page(((138, 141, 220, 260),))]
        box, = self.spans(notion_marker(200, 138), notion_marker(300, 138))
        self.assertEqual((round(box[1], 3), round(box[1] + box[3], 3)), (198.5, 300.0))
        self.assertEqual(round(box[2] + box[4], 3), 143.5)


def run_notion_rules(aux_text: str, decls: dict = doc.DECLARATIONS) -> check_items.Report:
    """Rule 11 on an .aux text, against a declarations.json."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "declarations.json"
        doc.write_json(path, decls)
        known = fc.notion_constants(path)
    report = check_items.Report()
    check_items.check_notions(fc.parse_aux(aux_text), known, report)
    return report


class TestNotionRule(unittest.TestCase):
    """Rule 11: a `\\fcnotion` names a constant declarations.json holds, once."""

    def test_a_declaration_and_a_reference_are_both_constants_a_notion_may_name(self):
        report = run_notion_rules("\\fc@auxnotion{1}{ExampleLib.GlueData}{gluing data}\n"
                                  "\\fc@auxnotion{2}{IsOpen}{open}\n")
        self.assertEqual((report.fails, report.warns), ([], []))

    def test_a_constant_declarations_json_does_not_hold(self):
        report = run_notion_rules("\\fc@auxnotion{1}{Mathlib.Imagined}{imagined}\n")
        self.assertEqual(rules_of(report), {11})
        self.assertIn("is not a declaration of declarations.json", report.fails[0])

    def test_a_notion_marked_twice(self):
        report = run_notion_rules("\\fc@auxnotion{1}{IsOpen}{open}\n"
                                  "\\fc@auxnotion{2}{IsOpen}{open again}\n")
        self.assertEqual(rules_of(report), {11})
        self.assertIn("is marked twice", report.fails[0])

    def test_a_notion_that_prints_nothing(self):
        report = run_notion_rules("\\fc@auxnotion{1}{IsOpen}{}\n")
        self.assertEqual(rules_of(report), {11})
        self.assertIn("prints nothing", report.fails[0])


def glyphs(spans: list[tuple], gaps: dict[int, float] | None = None) -> list:
    """The glyph list of `printed_box`, from ``(text, font, size, baseline shift)`` spans.

    Each span starts a twentieth of its size after the one before, which is the kerning
    of two characters the page sets side by side; `gaps[n]` is span n's own gap instead.
    White space is not a glyph: `box_glyphs` drops it, as the separators carry it.
    """
    out = []
    for n, (text, family, size, shift) in enumerate(spans):
        gap = (gaps or {}).get(n, 0.0 if n == 0 else 0.05 * size)
        for i, char in enumerate(text):
            if not char.isspace():
                out.append(check_items.Glyph(char=char, span=n, head=(i == 0), gap=gap,
                                             top=shift, bottom=size + shift, size=size,
                                             family=family))
    return out


class TestTheGlyphsOfABox(unittest.TestCase):
    """`printed_box`: pdftotext's crop with the characters and the spacing the page shows.

    The glyph lists are written out here rather than read from a PDF, so that every rule
    of the reader -- the fonts whose letters poppler flattens, the script runs, the three
    spaces that are poppler's and not the page's -- is checked without TeX or poppler.
    The reader on a real PDF is `TestTheChecksCatchARealBuild`'s.
    """

    def printed(self, spans, flat, gaps=None):
        return check_items.printed_box(glyphs(spans, gaps), flat)

    def test_a_double_struck_letter_and_a_script_capital(self):
        text, kept = self.printed([("N", "SVPXIN+MSBM10", 16.0, 0.0)], "N\n\n")
        self.assertEqual((text, kept), ("ℕ\n\n", []))
        text, _ = self.printed([("H", "TQXYZC+LMMathSymbols10", 16.0, 0.0)], "H")
        self.assertEqual(text, "ℋ")
        # a letter neither block names is taken from the block that has a hole for it
        text, _ = self.printed([("A", "ABCDEF+MSBM10", 16.0, 0.0)], "A")
        self.assertEqual(text, "𝔸")
        # and the same letters in a text font are themselves
        text, _ = self.printed([("NH", "WBJWHC+LMRoman10", 16.0, 0.0)], "NH")
        self.assertEqual(text, "NH")

    def test_the_operator_the_page_prints_for_the_font_s_character(self):
        text, _ = self.printed([("◦", "TQXYZC+LMMathSymbols10", 16.0, 0.0)], "◦")
        self.assertEqual(text, "∘")

    def test_a_subscript_run_every_character_of_which_has_one(self):
        """`Π1 (X)` is `Π₁(X)`: the digit is small and low, and the space is poppler's."""
        text, kept = self.printed(
            [("Π", "WBJWHC+LMRoman10", 16.0, 0.0), ("1", "ZVZNIX+LMRoman8", 12.0, 5.0),
             ("(X)", "WBJWHC+LMRoman10", 16.0, 0.0)], "Π1 (X)")
        self.assertEqual((text, kept), ("Π₁(X)", []))

    def test_a_script_run_Unicode_has_no_characters_for_is_poppler_s(self):
        """`ιS` is `ιS`, reported: Unicode has no small capital S."""
        text, kept = self.printed(
            [("ι", "DLETRZ+LMMathItalic10", 16.0, 0.0),
             ("S", "SCFNQQ+LMMathItalic8", 12.0, 5.0)], "ιS")
        self.assertEqual((text, kept), ("ιS", ["S"]))

    def test_display_labels_preserve_scripts_without_unicode_characters(self):
        for suffix, shift, expected in [("S", 5.0, "ι_{S}"),
                                        ("[r,s]", 5.0, "ι_{[r,s]}"),
                                        ("Q", -6.0, "ι^{Q}")]:
            spans = [("ι", "LMMathItalic10", 16.0, 0.0),
                     (suffix, "LMMathItalic8", 12.0, shift)]
            self.assertEqual(check_items.printed_box(
                glyphs(spans), "ι" + suffix, script_markup=True), (expected, []))

    def test_display_labels_preserve_nested_subscripts(self):
        spans = [("ι", "LMMathItalic10", 16.0, 0.0),
                 ("U", "LMMathItalic8", 12.0, 5.0),
                 ("i", "LMMathItalic6", 9.0, 9.0)]
        self.assertEqual(check_items.printed_box(
            glyphs(spans), "ιUi", script_markup=True), ("ι_{Uᵢ}", []))

    def test_a_superscript_run_is_read_the_other_way(self):
        text, kept = self.printed(
            [("τ", "DLETRZ+LMMathItalic10", 16.0, 0.0),
             ("−1", "HBAWBJ+LMMathSymbols8", 12.0, -6.0)], "τ−1")
        self.assertEqual((text, kept), ("τ⁻¹", []))

    def test_a_smaller_font_on_the_line_s_own_baseline_is_no_script(self):
        text, kept = self.printed(
            [("A", "WBJWHC+LMRoman10", 16.0, 0.0), ("1", "ZVZNIX+LMRoman8", 12.0, 4.0)], "A1")
        self.assertEqual((text, kept), ("A1", []))   # the two baselines are 16 and 16

    def test_a_binary_operator_stands_in_the_space_the_page_gives_it(self):
        """A space on each side where the page leaves one, and none where it does not."""
        spans = [("f", "DLETRZ+LMMathItalic10", 16.0, 0.0),
                 ("◦", "TQXYZC+LMMathSymbols10", 16.0, 0.0),
                 ("γ", "DLETRZ+LMMathItalic10", 16.0, 0.0)]
        # the page leaves a medium space, which poppler closed up on the right
        self.assertEqual(self.printed(spans, "f ◦γ", {1: 3.6, 2: 3.6})[0], "f ∘ γ")
        # and a kerned one, as a unary sign is, keeps the page's own nothing
        self.assertEqual(self.printed(spans, "f ◦ γ")[0], "f∘γ")

    def test_the_space_before_a_closing_parenthesis_is_poppler_s(self):
        text, _ = self.printed([("(f", "DLETRZ+LMMathItalic10", 16.0, 0.0),
                                (")", "WBJWHC+LMRoman10", 16.0, 0.0)], "(f )",
                               {1: 1.8})            # the italic f's side bearing
        self.assertEqual(text, "(f)")

    def test_a_word_space_at_a_script_run_s_edge_is_the_page_s(self):
        """The edge rule drops what the page does not show, never a word space."""
        spans = [("x", "DLETRZ+LMMathItalic10", 16.0, 0.0),
                 ("1", "ZVZNIX+LMRoman8", 12.0, 5.0),
                 ("and y", "WBJWHC+LMRoman10", 16.0, 0.0)]
        self.assertEqual(self.printed(spans, "x1 and y", {2: 5.3})[0], "x₁ and y")

    def test_a_line_break_is_left_as_pdftotext_broke_it(self):
        """Each line has its own size and baseline, and no rule reaches across a break."""
        spans = [("one", "WBJWHC+LMRoman10", 16.0, 0.0),
                 (")", "WBJWHC+LMRoman10", 16.0, 40.0)]
        self.assertEqual(self.printed(spans, "one\n)")[0], "one\n)")

    def test_the_tables_name_the_characters_the_page_can_be_written_with(self):
        self.assertEqual(check_items.SUBSCRIPTS["1"], "₁")
        self.assertEqual(check_items.SUPERSCRIPTS["1"], "¹")
        self.assertEqual(check_items.SUBSCRIPTS["i"], "ᵢ")
        self.assertNotIn("S", check_items.SUBSCRIPTS)   # Unicode has no small capital S
        self.assertNotIn("[", check_items.SUBSCRIPTS)


class TestRulesWithoutTeX(unittest.TestCase):
    """Rules 1 to 7 on a fake build of the synthetic items: clean, then one break each."""

    def test_the_fake_build_passes(self):
        report = run_rules(fake_aux())
        self.assertEqual((report.fails, report.warns), ([], []))

    def assertCaught(self, aux_text: str, rule: int, *, items: dict = doc.ITEMS,
                     hyps: dict = doc.HYPOTHESES, log: str = "", warning: bool = False):
        report = run_rules(aux_text, items, hyps, log)
        self.assertEqual(rules_of(report, warnings=warning), {rule},
                         report.fails + report.warns)
        if warning:
            self.assertEqual(report.fails, [])
            self.assertTrue(report.failed(strict=True))

    def test_rule_1(self):
        clean = fake_aux()
        self.assertCaught(clean.replace("\\fc@auxitem{1.1}{definition}", "\\fc@auxitem{1.1}{lemma}"), 1)
        self.assertCaught(clean.replace("{Gluing data}", "{Glueing data}"), 1)
        self.assertCaught(clean.replace("\\fc@auxitem{2.3}{corollary}{2}",
                                        "\\fc@auxitem{2.3}{corollary}{1}"), 1)
        self.assertCaught(clean + "\\fc@auxitem{2.4}{remark}{2}{An item left for later}\n", 1)
        self.assertCaught(clean + "\\fc@auxitem{2.9}{remark}{2}{Stray}\n", 1)
        self.assertCaught(clean.replace("{02-covers}{Covers}", "{02-covering}{Covers}"), 1)
        swapped = copy.deepcopy(doc.ITEMS)                      # 2.2 listed after 2.3
        swapped["items"][3], swapped["items"][4] = swapped["items"][4], swapped["items"][3]
        self.assertCaught(clean, 1, items=swapped)
        missing = re.sub(r"\\fc@auxitem\{2\.1\}.*?(?=\\fc@auxitem\{2\.2\})", "", clean, flags=re.S)
        self.assertCaught(missing, 1)

    def test_rule_2(self):
        clean = fake_aux()
        self.assertCaught(clean.replace("\\fc@auxpart{2.1}{b}\n", ""), 2)

    def test_rule_3(self):
        clean = fake_aux()
        self.assertCaught(clean + "\\fc@auxleanref{1.1}{}{ExampleLib.nowhere}\n", 3)
        self.assertCaught(clean + "\\fc@auxleanref{1.1}{}{ExampleLib.cover_univ}\n", 3,
                          warning=True)
        # a helper the item lists is no warning
        self.assertEqual(run_rules(clean + "\\fc@auxleanref{1.2}{b}{ExampleLib.glue_map}\n").warns,
                         [])

    def test_rule_4(self):
        clean = fake_aux()
        self.assertCaught(clean.replace("\\fc@auxleanref{1.2}{a}{ExampleLib.glue_theorem}", ""), 4)
        self.assertCaught(clean.replace("\\fc@auxleanref{1.2}{a}{ExampleLib.glue_theorem}",
                                        "\\fc@auxleanref{1.2}{}{ExampleLib.glue_theorem}"), 4)
        self.assertCaught(clean + "\\fc@auxleanref{2.1}{b}{ExampleLib.cover_mono}\n", 4)

    def test_rule_5(self):
        clean = fake_aux()
        self.assertCaught(clean.replace("\\fc@auxhyp{2.3}{}{open}", ""), 5)
        # in the part that needs it is enough
        moved = clean.replace("\\fc@auxhyp{1.2}{}{open}\n", "") + (
            "\\fc@auxhyp{1.2}{a}{open}\n\\fc@auxhyp{1.2}{b}{open}\n")
        self.assertEqual(rules_of(run_rules(moved), warnings=True), set())
        self.assertCaught(clean + "\\fc@auxhyp{2.1}{a}{open}\n", 5, warning=True)
        self.assertCaught(clean.replace("\\fc@auxhypdef{open}{\\hypOpen}", ""), 5)
        self.assertCaught(clean.replace("\\fc@auxhypdef{open}{\\hypOpen}",
                                        "\\fc@auxhypdef{open}{\\hypIsOpen}"), 5)
        self.assertCaught(clean + "\\fc@auxhypdef{closed}{\\hypClosed}\n", 5, warning=True)
        bad = copy.deepcopy(doc.HYPOTHESES)
        bad["hypotheses"].append({"key": "open", "macro": "hypOpen", "binder_head": "Is Open"})
        self.assertCaught(clean, 5, hyps=bad)

    def test_rule_5_by_a_printed_type(self):
        by_type = copy.deepcopy(doc.HYPOTHESES)
        del by_type["hypotheses"][0]["binder_head"]
        by_type["hypotheses"][0]["binder_type"] = r"IsOpen [UV]"
        self.assertEqual(run_rules(fake_aux(), hyps=by_type).fails, [])
        by_type["hypotheses"][0]["binder_type"] = r"IsClosed .*"   # nothing binds it
        self.assertCaught(fake_aux(), 5, hyps=by_type, warning=True)
        neither = copy.deepcopy(by_type)
        del neither["hypotheses"][0]["binder_type"]
        self.assertCaught(fake_aux(), 5, hyps=neither)

    def test_rule_5_reads_no_hypothesis_out_of_a_conclusion(self):
        # cover_univ states an equivalence; IsOpen on its right is no hypothesis it binds
        decls = copy.deepcopy(doc.DECLARATIONS)
        for d in decls["declarations"]:
            if d["name"] == "ExampleLib.cover_univ":
                d["signature"] = ("∀ {X : Type u_1} [inst : TopologicalSpace X] (W : Set X), "
                                  "ExampleLib.Covers Set.univ W ↔ ∀ (s : Set X), IsOpen s → s ⊆ W")
        report = run_rules(fake_aux(), decls=decls)
        self.assertEqual((report.fails, report.warns), ([], []))

    def test_rule_5_counts_a_hypothesis_items_json_lists(self):
        items = copy.deepcopy(doc.ITEMS)
        lemma = items["items"][2]
        lemma["declarations"][0]["hypotheses"] = ["hU : IsOpen U"]   # cover_mono's signature does not
        self.assertCaught(fake_aux(), 5, items=items)

    def test_rule_6(self):
        clean = fake_aux()
        self.assertCaught(clean.replace("{Theorem 4.1}{*}", "{Theorem 4.1}{}"), 6)
        self.assertCaught(clean.replace("{Definition 2.3}{}", "{Definition 2.4}{}"), 6)
        self.assertCaught(clean.replace("\\fc@auxref{1.1}{}{Definition 2.3}{}", ""), 6)
        self.assertCaught(clean + "\\fc@auxref{2.1}{a}{Lemma 1.1}{}\n", 6)
        self.assertCaught(clean + "\\fc@auxref{1.1}{}{Definition 2.3}{}\n", 6)
        items = copy.deepcopy(doc.ITEMS)                        # a number on part (b) only
        items["items"][2]["parts"][1]["reference"] = {"number": "Lemma 3.2", "source": "docstring",
                                                     "declaration": "ExampleLib.cover_univ"}
        self.assertCaught(clean, 6, items=items)
        self.assertEqual(run_rules(clean + "\\fc@auxref{2.1}{b}{Lemma 3.2}{}\n", items).fails, [])

    def test_rule_7(self):
        clean = fake_aux()
        self.assertCaught(clean, 7, log="LaTeX Warning: Label `item:1.1' multiply defined.\n")
        log = "LaTeX Warning: Reference `item:1.2:a' on page 2 undefined on input line 9.\n"
        self.assertCaught(clean, 7, log=log, warning=True)
        # a reference into a section this build skipped is no warning
        partial = clean.replace("\\fc@auxsection{1}{1}", "\\fc@auxsection{1}{0}")
        report = run_rules(partial, log=log)
        self.assertNotIn(7, rules_of(report, warnings=True))
        cite = "LaTeX Warning: Citation `reference' on page 1 undefined on input line 3.\n"
        self.assertCaught(clean, 7, log=cite, warning=True)


# ---------------------------------------------------------------------------
# the whole runtime, with TeX and poppler

def missing_programs() -> list[str]:
    missing = [p for p in TEX_PROGRAMS + POPPLER if shutil.which(p) is None]
    if not missing:
        for name in doc.TEX_PACKAGES:
            if subprocess.run(["kpsewhich", name], capture_output=True).returncode != 0:
                missing.append(name)
    return missing


def need_tex(case: unittest.TestCase) -> None:
    missing = missing_programs()
    if missing:
        why = "TeX or poppler is missing: " + ", ".join(missing)
        if os.environ.get("CF_REQUIRE_TEX") == "1":
            case.fail(f"{why}, and CF_REQUIRE_TEX=1 requires them")
        raise unittest.SkipTest(why)


class _Built(unittest.TestCase):
    """A synthetic repository, and helpers to build and check it."""

    tmp: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        need_tex(self)

    def repo(self, name: str, **changes) -> Path:
        return doc.write_repo(self.root / name, **changes)

    @staticmethod
    def tool(repo: Path, *argv: str, script: str | None = None) -> subprocess.CompletedProcess:
        """Run a tool from the repository's root, as the brief says to."""
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
        if script is None:
            cmd = ["bash", "informal/tools/build_pdf.sh", *argv]
        else:
            cmd = [sys.executable, f"informal/tools/{script}.py", *argv]
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env,
                              timeout=600)

    def build(self, repo: Path, outdir: Path, *flags: str, ok: bool = True):
        out = self.tool(repo, *flags, str(outdir))
        if ok:
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out

    def check(self, repo: Path, outdir: Path, *flags: str) -> subprocess.CompletedProcess:
        return self.tool(repo, str(outdir), *flags, script="check_items")


class TestTheRuntimeBuilds(_Built):
    """The clean document: builds, boxes, checks and sheets."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if missing_programs():
            return
        cls.repo_ = doc.write_repo(cls.root / "repo")
        cls.a, cls.b = cls.root / "a", cls.root / "b"
        for outdir in (cls.a, cls.b):
            subprocess.run(["bash", "informal/tools/build_pdf.sh", "--clean", "--source-commit",
                            "f" * 40, str(outdir)], cwd=cls.repo_, check=True,
                           capture_output=True)
        subprocess.run([sys.executable, "informal/tools/extract_markers.py", str(cls.a)],
                       cwd=cls.repo_, check=True, capture_output=True)

    def test_two_clean_builds_are_byte_identical(self):
        pdf_a, pdf_b = (d / f"{doc.JOB}.pdf" for d in (self.a, self.b))
        self.assertEqual(pdf_a.read_bytes(), pdf_b.read_bytes())
        receipt = json.loads((self.a / "build-receipt.json").read_text())
        self.assertEqual(receipt["pdf"]["sha256"], fc.sha256_file(pdf_a))
        self.assertEqual(receipt["source_commit"], "f" * 40)
        self.assertEqual(receipt["source_date_epoch"], doc.CONFIG["source_date_epoch"])
        self.assertTrue(receipt["pdftex"].startswith("pdfTeX"))
        # the marker package is the runtime's, from OUTDIR/runtime/, and the sections the copy's
        self.assertIn("runtime/fcmarkers.sty", receipt["inputs"])
        self.assertIn("src/sections/02-covers.tex", receipt["inputs"])
        self.assertNotIn("src/fcmarkers.sty", receipt["inputs"])
        info = subprocess.run(["pdfinfo", str(pdf_a)], capture_output=True, text=True).stdout
        self.assertRegex(info, r"Title:\s+The glue theorem, after ExampleLib\n")
        self.assertNotIn(b"/ID [", pdf_a.read_bytes())         # \pdftrailerid{}
        self.assertNotIn(b"PTEX.Fullbanner", pdf_a.read_bytes())

    def test_the_markers_hold_one_box_per_page_of_every_item_and_part(self):
        markers = json.loads((self.a / "markers.json").read_text())
        self.assertEqual(markers["schema"], fc.MARKERS_SCHEMA)
        self.assertEqual(markers["pdf_sha256"], fc.sha256_file(self.a / f"{doc.JOB}.pdf"))
        self.assertEqual(markers["source_commit"], "f" * 40)
        self.assertEqual(markers["boxes_config"], doc.CONFIG["boxes"])
        entries = {e["id"]: e for e in markers["entries"]}
        self.assertEqual(list(entries), ["1.1", "1.2", "1.2(a)", "1.2(b)", "2.1", "2.1(a)",
                                         "2.1(b)", "2.2", "2.2(a)", "2.2(b)", "2.3"])
        self.assertEqual(entries["1.2"]["parts"], ["1.2(a)", "1.2(b)"])
        self.assertEqual(entries["1.2(b)"]["decls"],
                         ["ExampleLib.glue_surjective", "ExampleLib.glue_map"])
        self.assertEqual(entries["2.3"]["title"], "Counting glued pieces")
        # example 2.2 crosses the page break: one box on each page, for it and its part (a)
        self.assertEqual([b[0] for b in entries["2.2"]["boxes"]], [1, 2])
        self.assertEqual([b[0] for b in entries["2.2(a)"]["boxes"]], [1, 2])
        for entry in markers["entries"]:
            for page, x, y, w, h in entry["boxes"]:
                with self.subTest(entry=entry["id"]):
                    self.assertTrue(0 <= x and 0 <= y and w > 0 and h > 0)
                    self.assertLessEqual(x + w, 1)
                    self.assertLessEqual(y + h, 1)
        # consecutive parts share an edge, inside their item
        a, b = entries["1.2(a)"]["boxes"][0], entries["1.2(b)"]["boxes"][0]
        self.assertAlmostEqual(a[2] + a[4], b[2], places=5)

    def test_the_checks_and_the_proof_sheets_pass(self):
        out = self.check(self.repo_, self.a, "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("check_items: PASS (all sections: 5 of 5 items, 6 parts, 13 boxes;", out.stdout)
        sheets = self.root / "sheets"
        out = self.tool(self.repo_, str(self.a), "--dest", str(sheets), script="proof_sheets")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("0 ink under a stroke, 0 cut word, 0 stray item text, "
                      "0 ink outside its boxes, 0 frames too close", out.stdout)
        self.assertEqual(sorted(p.name for p in sheets.iterdir()),
                         ["page-01.png", "page-01.svg", "page-02.png", "page-02.svg", "render"])
        self.assertTrue((sheets / "page-01.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_the_proof_sheets_pass_wherever_the_page_breaks(self):
        # tall and deep lines at every boundary, with the page break slid through them: the
        # edges where \lineskip leaves 2pt between two lines are tight, never fatal
        def one(n: int) -> list[str]:
            repo = self.repo(f"stress-{n}", sections={"02-covers": doc.stress_section_2(n)})
            outdir = self.root / f"stress-{n}-out"
            failed = []
            for argv, script in (([str(outdir)], None), ([str(outdir)], "extract_markers"),
                                 ([str(outdir), "--strict"], "check_items"),
                                 ([str(outdir)], "proof_sheets")):
                out = self.tool(repo, *argv, script=script)
                if out.returncode:
                    failed.append(f"{n} sentences, {script or 'build_pdf'}: "
                                  f"{out.stdout[-600:]}{out.stderr[-600:]}")
                    break
            return failed
        with ThreadPoolExecutor(4) as pool:
            failed = [f for got in pool.map(one, (2, 7, 13, 18)) for f in got]
        self.assertEqual(failed, [])

    def test_no_tool_leaves_bytecode_in_the_repository(self):
        self.assertEqual(list(self.repo_.rglob("__pycache__")), [])
        self.assertEqual(list(self.a.rglob("__pycache__")), [])

    def test_a_box_that_holds_no_text_or_overlaps_is_caught(self):
        outdir = self.root / "boxes"
        shutil.copytree(self.a, outdir)
        markers = json.loads((outdir / "markers.json").read_text())
        entries = {e["id"]: e for e in markers["entries"]}
        entries["1.1"]["boxes"][0][2] = 0.02                     # in the top margin: no text
        box = entries["2.1"]["boxes"][0]                         # down through 2.2's head
        box[4] = entries["2.2"]["boxes"][0][2] + 0.008 - box[2]
        (outdir / "markers.json").write_text(json.dumps(markers))
        out = self.check(self.repo_, outdir)
        self.assertEqual(out.returncode, 1)
        self.assertIn("FAIL [9] 1.1: box 1 (page 1) holds no text", out.stdout)
        self.assertIn("FAIL [10] 2.1: its boxes overlap those of item 2.2", out.stdout)
        # and a proof sheet sees a stroke through the text of 2.2
        sheets = self.tool(self.repo_, str(outdir), script="proof_sheets")
        self.assertEqual(sheets.returncode, 1, sheets.stdout)
        self.assertIn("INK UNDER A STROKE: p.1 2.1: bottom edge", sheets.stdout)

    def test_markers_of_another_build_are_caught(self):
        out = self.check(self.repo_, self.b)                      # b was never extracted
        self.assertEqual(out.returncode, 1)
        self.assertIn("FAIL [8] markers:", out.stdout)
        outdir = self.root / "stale"
        shutil.copytree(self.a, outdir)
        markers = json.loads((outdir / "markers.json").read_text())
        markers["entries"][0]["decls"] = []
        (outdir / "markers.json").write_text(json.dumps(markers))
        out = self.check(self.repo_, outdir)
        self.assertIn("FAIL [8] markers: the entries of", out.stdout)

    def test_aux_only_needs_no_markers(self):
        out = self.check(self.repo_, self.b, "--aux-only", "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class TestPartialBuilds(_Built):

    def test_one_section_builds_and_checks_alone(self):
        repo = self.repo("partial")
        outdir = self.root / "partial-out"
        self.build(repo, outdir, "--only", "2")
        receipt = json.loads((outdir / "build-receipt.json").read_text())
        self.assertEqual((receipt["only"], receipt["sections"]), (["2"], ["2"]))
        # 2.3 cites 1.2(a), in the section this build skipped: no warning
        out = self.check(repo, outdir, "--aux-only", "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("(section 2: 3 of 3 items", out.stdout)
        out = self.check(repo, outdir, "--section", "1", "--aux-only")
        self.assertEqual(out.returncode, 2)
        # then the whole document into the same directory: the stamp resets latexmk's state
        self.build(repo, outdir)
        out = self.check(repo, outdir, "--aux-only", "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("(all sections: 5 of 5 items", out.stdout)

    def test_a_hypothesis_macro_keeps_the_space_after_it(self):
        """`\\hypOpen and` prints "... open and", not "... openand": TeX drops the space
        after a control word and `\\@ifstar` drops one before the token it looks at, so
        `\\fcdefhyp` ends the words with `\\xspace`."""
        if any(shutil.which(p) is None for p in POPPLER):     # pragma: no cover
            self.skipTest("poppler is not installed")
        section = r"""
\begin{lemma}[id=1.1]{Spacing}
Let $U$ and $V$ be subsets of $X$, suppose that \hypOpen and that $X$ is a space,
and recall that \hypOpen* holds here too.
\leanref{ExampleLib.GlueData}
\end{lemma}
"""
        repo = self.repo("spacing", sections={"01-gluing-spaces": section, "02-covers": ""})
        outdir = self.root / "spacing-out"
        self.build(repo, outdir, "--only", "1")
        text = subprocess.run(["pdftotext", str(outdir / f"{doc.JOB}.pdf"), "-"],
                              capture_output=True, text=True, check=True).stdout
        flat = " ".join(text.split())
        self.assertIn("U and V are open and that X is a space", flat)
        self.assertIn("are open holds here too", flat)

    def test_a_section_number_is_a_number_and_must_exist(self):
        repo = self.repo("numbers")
        outdir = self.root / "numbers-out"
        # sections.tex and --only both write 02, and 02 is section 2
        (repo / "informal" / "sections.tex").write_text(
            doc.SECTIONS_TEX.replace("\\fcinputsection{2}", "\\fcinputsection{02}"))
        self.build(repo, outdir, "--only", "02")
        receipt = json.loads((outdir / "build-receipt.json").read_text())
        self.assertEqual((receipt["only"], receipt["sections"]), (["2"], ["2"]))
        self.assertEqual((outdir / "build-only.txt").read_text(), "2")
        out = self.check(repo, outdir, "--aux-only", "--strict")
        self.assertIn("(section 2: 3 of 3 items", out.stdout)
        # a section sections.tex does not list is refused, and a build of no section
        # passes no check
        out = self.build(repo, outdir, "--only", "7", ok=False)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("--only 7: no such section was typeset", out.stderr)
        out = self.check(repo, outdir, "--aux-only", "--strict")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("FAIL [1] sections: the checked scope (no section) holds no item",
                      out.stdout)
        out = self.build(repo, outdir, "--only", "0", ok=False)
        self.assertEqual(out.returncode, 2)

    def test_the_conventions_phase_checks_its_build(self):
        empty = {"01-gluing-spaces": "", "02-covers": ""}
        repo = self.repo("conventions", sections=empty)
        outdir = self.root / "conventions-out"
        self.build(repo, outdir)
        out = self.check(repo, outdir, "--conventions", "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        repo = self.repo("conventions-bad", sections=empty, notation_extra="")
        self.build(repo, outdir, "--clean")
        out = self.check(repo, outdir, "--conventions")
        self.assertEqual(out.returncode, 1)
        self.assertIn("FAIL [5] notation: no \\fcdefhyp for the hypothesis open", out.stdout)


class TestStandingAssumptions(_Built):
    def test_section_assumption_satisfies_the_item_and_its_parts(self):
        section = doc.SECTION_1.replace(
            r"\begin{theorem}[id=1.2]",
            "\\begin{fcassumptions}\nSuppose that \\hypOpen.\n"
            r"\begin{theorem}[id=1.2]").replace(
                r", and suppose that \hypOpen.", ".")
        section += "\n\\end{fcassumptions}\n"
        repo = self.repo("standing-parts", sections={"01-gluing-spaces": section})
        outdir = self.root / "standing-parts-out"
        self.build(repo, outdir)
        out = self.check(repo, outdir, "--aux-only", "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        aux = fc.read_aux(outdir / f"{doc.JOB}.aux")
        self.assertEqual([h for h in aux.hyps if h["id"] == "1.2"],
                         [{"id": "1.2", "part": "", "key": "open"}])
        self.assertFalse(any(h["id"] in {"1.1", "2.1", "2.2"} for h in aux.hyps))

    def test_nested_scopes_cover_later_items_then_restore_the_outer_scope(self):
        section = r"""
\begin{fcassumptions}
Suppose that \hypOpen.
\begin{fcassumptions}
Suppose also that \hypNonempty, and recall that \hypOpen.
\begin{lemma}[id=1.1]{Inner result} Inner. \end{lemma}
\end{fcassumptions}
\begin{lemma}[id=1.2]{Outer result} Outer. \end{lemma}
\begin{lemma}[id=1.3]{Another outer result} Outer again. \end{lemma}
\end{fcassumptions}
\begin{lemma}[id=1.4]{Independent result} Outside. \end{lemma}
"""
        repo = self.repo("standing-nested", sections={"01-gluing-spaces": section,
                         "02-covers": ""}, notation_extra=doc.NOTATION_HYPOTHESES +
                         r"\fcdefhyp{\hypNonempty}{nonempty}{$U$ is nonempty}" + "\n")
        outdir = self.root / "standing-nested-out"
        self.build(repo, outdir, "--only", "1")
        aux = fc.read_aux(outdir / f"{doc.JOB}.aux")
        self.assertEqual([(h["id"], h["key"]) for h in aux.hyps],
                         [("1.1", "open"), ("1.1", "nonempty"),
                          ("1.2", "open"), ("1.3", "open")])

    def test_starred_mention_does_not_supply_a_standing_assumption(self):
        section = doc.SECTION_1.replace(
            r"\begin{theorem}[id=1.2]",
            "\\begin{fcassumptions}\nRecall that \\hypOpen*.\n"
            r"\begin{theorem}[id=1.2]").replace(
                r", and suppose that \hypOpen.", ".")
        section += "\n\\end{fcassumptions}\n"
        repo = self.repo("standing-star", sections={"01-gluing-spaces": section})
        outdir = self.root / "standing-star-out"
        self.build(repo, outdir)
        out = self.check(repo, outdir, "--aux-only", "--strict")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("\\hypOpen is missing", out.stdout)

    def test_standing_scope_cannot_begin_inside_a_statement(self):
        section = r"""
\begin{lemma}[id=1.1]{Invalid scope}
\begin{fcassumptions} Suppose that \hypOpen. \end{fcassumptions}
\end{lemma}
"""
        repo = self.repo("standing-invalid", sections={"01-gluing-spaces": section})
        outdir = self.root / "standing-invalid-out"
        out = self.build(repo, outdir, "--only", "1", ok=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("Standing assumptions cannot begin inside an item", out.stdout + out.stderr)


class TestTheConventionsNotions(_Built):
    """The notions of the conventions: the words alone on the page, a box around each."""

    def built(self, name: str, extra: str = doc.CONVENTIONS_NOTIONS):
        repo = self.repo(name, conventions_extra=extra)
        outdir = self.root / f"{name}-out"
        self.build(repo, outdir)
        return repo, outdir

    def test_the_boxes_hold_the_words_and_no_lean_name_is_printed(self):
        if any(shutil.which(p) is None for p in POPPLER):     # pragma: no cover
            self.skipTest("poppler is not installed")
        repo, outdir = self.built("notions")
        out = self.tool(repo, str(outdir), script="extract_markers")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("3 notions", out.stdout)
        markers = json.loads((outdir / "markers.json").read_text())
        entries = {e["id"]: e for e in markers["entries"] if "notion" in e}
        self.assertEqual(sorted(entries), ["notion:ExampleLib.GlueData", "notion:IsOpen",
                                           "notion:Set"])
        self.assertEqual(entries["notion:IsOpen"]["words"], "open")
        self.assertEqual(entries["notion:IsOpen"]["number"], "n2")
        pdf = outdir / f"{doc.JOB}.pdf"
        sizes = fc.pdf_pages(pdf)
        for entry in entries.values():
            with self.subTest(notion=entry["notion"]):
                self.assertTrue(entry["boxes"])
                cropped = " ".join(check_items.crop_text(pdf, box, sizes[int(box[0]) - 1], 288)
                                   for box in entry["boxes"])
                for word in re.findall(r"[A-Za-z]{3,}", entry["words"]):
                    self.assertIn(word.lower(), cropped.lower())
        # the constants are the marker's argument alone: no page prints one
        text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True,
                              check=True).stdout
        self.assertNotIn("ExampleLib.GlueData", text)
        self.assertNotIn("IsOpen", text)
        out = self.check(repo, outdir, "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        sheets = self.tool(repo, str(outdir), "--dest", str(self.root / "notion-sheets"),
                           script="proof_sheets")
        self.assertEqual(sheets.returncode, 0, sheets.stdout + sheets.stderr)
        self.assertIn("0 ink under a stroke, 0 cut word", sheets.stdout)

    def test_a_constant_declarations_json_does_not_hold_fails_rule_11(self):
        repo, outdir = self.built(
            "notions-unknown",
            extra="\nA \\fcnotion{Mathlib.Imagined}{wish} is nowhere.\n")
        out = self.check(repo, outdir, "--aux-only", "--strict")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("FAIL [11] notion Mathlib.Imagined: is not a declaration of", out.stdout)

    def test_the_conventions_phase_checks_the_notions_too(self):
        repo = self.repo("notions-conventions", sections={"01-gluing-spaces": "",
                                                          "02-covers": ""},
                         conventions_extra=doc.CONVENTIONS_NOTIONS)
        outdir = self.root / "notions-conventions-out"
        self.build(repo, outdir)
        out = self.check(repo, outdir, "--conventions", "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("(conventions: 1 hypotheses, 3 notions;", out.stdout)

    def test_a_notion_marked_twice_stops_the_build(self):
        twice = doc.CONVENTIONS_NOTIONS + "\nAgain \\fcnotion{IsOpen}{open}.\n"
        repo = self.repo("notions-twice", conventions_extra=twice)
        out = self.build(repo, self.root / "notions-twice-out", ok=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("The notion IsOpen is marked twice", out.stdout + out.stderr)

    def test_notion_links_work_at_their_natural_occurrence_inside_an_item(self):
        inside = doc.SECTION_1.replace("Gluing data on $X$",
                                       "\\fcnotion{ExampleLib.GlueData}{Gluing data} on $X$")
        inside = inside.replace("The square formed by $U", "The square formed by the \\fcnotion{Set}{subsets} $U")
        repo = self.repo("notions-in-item", sections={"01-gluing-spaces": inside})
        outdir = self.root / "notions-in-item-out"
        self.build(repo, outdir)
        out = self.tool(repo, str(outdir), script="extract_markers")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        entries = json.loads((outdir / "markers.json").read_text())["entries"]
        notion = next(e for e in entries if e.get("notion") == "ExampleLib.GlueData")
        self.assertEqual(notion["words"], "Gluing data")
        self.assertTrue(notion["boxes"])
        self.assertTrue(next(e for e in entries if e.get("notion") == "Set")["boxes"])
        out = self.check(repo, outdir, "--strict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        sheets = self.tool(repo, str(outdir), "--dest", str(self.root / "inline-sheets"),
                           script="proof_sheets")
        self.assertEqual(sheets.returncode, 0, sheets.stdout + sheets.stderr)
        self.assertIn("0 ink under a stroke, 0 cut word", sheets.stdout)


class TestTheBuildRefuses(_Built):

    def test_each_marker_error_stops_the_build(self):
        theorem = doc.SECTION_1[doc.SECTION_1.index("\\begin{theorem}"):]
        cases = {
            "an id used twice": (doc.SECTION_1.replace("[id=1.2]", "[id=1.1]"),
                                 "Item 1.1 appears twice"),
            "an item inside an item": (doc.SECTION_1.replace("\\end{definition}", "") + "\n"
                                       "\\end{definition}\n", "is still open"),
            "an item without an id": (doc.SECTION_1.replace("[id=1.1]", ""),
                                       "Item without an id"),
            "a part out of order": (theorem.replace("\\itempart{b}", "\\itempart{c}"),
                                    "Part (c) of item 1.2 should be (b)"),
            "a part outside an item": ("\\itempart{a}{x}\n", "outside an item"),
            "a declaration outside an item": ("\\leanref{ExampleLib.GlueData}\n",
                                              "outside an item"),
        }
        for what, (body, message) in cases.items():
            with self.subTest(case=what):
                repo = self.repo(f"error-{len(os.listdir(self.root))}",
                                 sections={"01-gluing-spaces": body})
                out = self.build(repo, self.root / "error-out", ok=False)
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertIn(message, out.stderr)
                # the error lines alone, never the source lines TeX shows around them
                self.assertNotRegex(out.stderr, r"(?m)^l\.\d+ ")

    def test_a_hypothesis_macro_that_clashes_stops_the_build(self):
        repo = self.repo("clash", notation_extra=doc.NOTATION_HYPOTHESES
                         + "\\fcdefhyp{\\hypAlso}{open}{again}\n")
        out = self.build(repo, self.root / "clash-out", ok=False)
        self.assertIn("Hypothesis `open' is defined twice", out.stderr)
        repo = self.repo("clash2", notation_extra="\\fcdefhyp{\\section}{open}{x}\n")
        out = self.build(repo, self.root / "clash2-out", ok=False)
        self.assertIn("\\section is already defined", out.stderr)

    def test_sections_out_of_order_stop_the_build(self):
        repo = self.repo("order")
        (repo / "informal" / "sections.tex").write_text(
            "\\fcinputsection{2}{02-covers}{Covers}\n"
            "\\fcinputsection{1}{01-gluing-spaces}{Gluing spaces}\n")
        out = self.build(repo, self.root / "order-out", ok=False)
        self.assertIn("Section 1 comes after section 2", out.stderr)

    def test_the_source_may_not_reach_outside_its_copy(self):
        # OUTDIR/build-only.txt exists, so only openin_any=p keeps ../ from reading it
        for what, line in (("an absolute path", "\\input{/etc/hosts}"),
                           ("a parent directory", "\\input{../build-only.txt}")):
            with self.subTest(case=what):
                repo = self.repo(f"reach-{len(os.listdir(self.root))}",
                                 sections={"02-covers": doc.SECTION_2 + line + "\n"})
                out = self.build(repo, self.root / "reach-out", ok=False)
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertIn("not found", out.stderr)

    def test_a_file_the_source_writes_into_outdir_is_refused(self):
        # TeX reads a file of OUTDIR before it searches TEXINPUTS: a section that writes a
        # fcmarkers.sty there would replace the runtime's on the next pass
        shadow = (doc.SECTION_2 + "\\newwrite\\shadow\\immediate\\openout\\shadow=fcmarkers.sty\n"
                  "\\immediate\\write\\shadow{\\string\\typeout{SHADOW}"
                  "\\string\\input{runtime/fcmarkers.sty}}\\immediate\\closeout\\shadow\n"
                  "\\immediate\\openout\\shadow=runtime/note.tex\\immediate\\closeout\\shadow\n")
        outdir = self.root / "shadow-out"
        out = self.build(self.repo("shadow", sections={"02-covers": shadow}), outdir, ok=False)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        for what in (f"read {outdir.resolve()}/fcmarkers.sty",
                     f"wrote {outdir.resolve()}/fcmarkers.sty",
                     f"wrote {outdir.resolve()}/runtime/note.tex"):
            self.assertIn(what, out.stderr)
        # and a file left in OUTDIR refuses the next build into it until it is removed
        repo = self.repo("after-shadow")
        out = self.build(repo, outdir, ok=False)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn(f"read {outdir.resolve()}/fcmarkers.sty", out.stderr)
        (outdir / "fcmarkers.sty").unlink()
        self.build(repo, outdir, "--clean")
        receipt = json.loads((outdir / "build-receipt.json").read_text())
        self.assertEqual([k for k in receipt["inputs"] if "fcmarkers" in k],
                         ["runtime/fcmarkers.sty"])

    def test_a_stopped_build_stops_tex_and_frees_outdir(self):
        # a section that loops: TERM must stop latexmk and pdflatex, not wait for them
        loop = "\\begin{lemma}[id=2.1]{Monotonicity of covers}\\def\\x{\\x}\\x\\end{lemma}\n"
        repo = self.repo("loop", sections={"02-covers": loop})
        outdir = (self.root / "loop-out").resolve()

        def tex() -> list[str]:
            ps = subprocess.run(["ps", "-A", "-o", "pid=,args="], capture_output=True,
                                text=True).stdout
            return [line for line in ps.splitlines() if str(outdir) in line
                    and ("pdflatex" in line or "latexmk" in line)]
        proc = subprocess.Popen(["bash", "informal/tools/build_pdf.sh", str(outdir)], cwd=repo,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        try:
            deadline = time.monotonic() + 60
            while not any("pdflatex" in line for line in tex()):
                self.assertLess(time.monotonic(), deadline, "pdflatex never started")
                time.sleep(0.2)
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=30), 143)
            deadline = time.monotonic() + 10
            while tex() and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertEqual(tex(), [])
            self.assertFalse((outdir / ".build-lock").exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for line in tex():
                os.kill(int(line.split()[0]), signal.SIGKILL)

    def test_a_symbolic_link_is_refused(self):
        repo = self.repo("link")
        (repo / "informal" / "sections" / "03-other.tex").symlink_to("/etc/hosts")
        out = self.build(repo, self.root / "link-out", ok=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("symbolic link", out.stderr)

    def test_shell_escape_is_off(self):
        marker = self.root / "shell-escape-ran"
        repo = self.repo("shell", sections={
            "02-covers": doc.SECTION_2 + f"\\immediate\\write18{{touch {marker}}}\n"})
        self.build(repo, self.root / "shell-out")
        self.assertFalse(marker.exists())
        # TeX Live's default, restricted shell escape, would refuse `touch` too; the log
        # says whether any shell escape was on
        log = (self.root / "shell-out" / f"{doc.JOB}.log").read_text(errors="replace")
        self.assertNotIn("write18 enabled", log)

    def test_no_latexmkrc_is_read(self):
        # a latexmkrc in the copy is Perl that latexmk would run; -norc keeps it unread
        marker = self.root / "latexmkrc-ran"
        repo = self.repo("rc")
        (repo / "informal" / "latexmkrc").write_text(
            f"open(my $fh, '>', '{marker}'); close($fh);\n")
        self.build(repo, self.root / "rc-out")
        self.assertFalse(marker.exists())

    def test_an_outdir_that_holds_other_files_is_refused(self):
        repo = self.repo("outdirs")
        # the repository itself, or a directory that holds it: each build replaces
        # OUTDIR/src/ and OUTDIR/runtime/
        for outdir in (repo, repo.parent):
            with self.subTest(outdir=str(outdir)):
                out = self.build(repo, outdir, ok=False)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn("may not be the current directory or hold it", out.stderr)
        self.assertTrue((repo / "informal" / "main.tex").is_file())
        occupied = self.root / "occupied"
        (occupied / "src").mkdir(parents=True)
        (occupied / "src" / "keep.txt").write_text("keep")
        out = self.build(repo, occupied, ok=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("is not empty and holds no earlier build", out.stderr)
        self.assertTrue((occupied / "src" / "keep.txt").is_file())
        # one build at a time: a held lock refuses a second
        busy = self.root / "busy"
        self.build(repo, busy)
        self.assertFalse((busy / ".build-lock").exists())
        (busy / ".build-lock").mkdir()
        out = self.build(repo, busy, ok=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("another build is using", out.stderr)
        self.assertTrue((busy / "src" / "main.tex").is_file())

    def test_bad_arguments_and_settings_are_refused(self):
        repo = self.repo("args")
        out = self.build(repo, repo / "informal" / "build", ok=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("OUTDIR may not be inside informal/", out.stderr)
        out = self.build(repo, self.root / "args-out", "--only", "1,,2", ok=False)
        self.assertEqual(out.returncode, 2)
        out = self.build(repo, self.root / "args-out", "--source-commit", "abc", ok=False)
        self.assertEqual(out.returncode, 2)
        null = self.repo("null-time", config={**doc.CONFIG, "source_date_epoch": None})
        out = self.build(null, self.root / "null-out", ok=False)
        self.assertEqual(out.returncode, 2)
        self.assertIn("source_date_epoch is null", out.stderr)
        absent = self.repo("absent", config={**doc.CONFIG, "tex_packages": [
            *doc.TEX_PACKAGES, "no-such-package-here.sty"]})
        out = self.build(absent, self.root / "absent-out", ok=False)
        self.assertEqual(out.returncode, 3)
        self.assertIn("TeX cannot find: no-such-package-here.sty", out.stderr)
        out = subprocess.run(["bash", str(TOOLS / "build_pdf.sh"), str(self.root / "x")],
                             cwd=self.root, capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("no informal/ here", out.stderr)


class TestTheChecksCatchARealBuild(_Built):
    """The rules on a build whose sections break five of them at once."""

    def test_each_break_is_reported_under_its_rule(self):
        section_1 = (doc.SECTION_1
                     .replace("\\begin{definition}[id=1.1]", "\\begin{lemma}[id=1.1]")
                     .replace("\\end{definition}", "\\end{lemma}")            # rule 1
                     .replace("\\fcref*{Theorem 4.1}", "\\fcref{Theorem 4.1}")  # rule 6
                     .replace(", and suppose that \\hypOpen", ""))             # rule 5
        section_2 = (doc.SECTION_2
                     .replace("\\leanref{ExampleLib.cover_univ}",
                              "\\leanref{ExampleLib.nowhere}")                 # rules 3, 4
                     .replace("\\itempart{b}{%\n  If the chain", "\\itempart{b}{%\n"
                              "  \\leanref{ExampleLib.chain_long}If the chain"))  # rule 4
        repo = self.repo("broken", sections={"01-gluing-spaces": section_1,
                                             "02-covers": section_2})
        outdir = self.root / "broken-out"
        self.build(repo, outdir)
        out = self.check(repo, outdir, "--aux-only")
        self.assertEqual(out.returncode, 1)
        for line in ("FAIL [1] 1.1: typeset as lemma, but items.json says definition",
                     "FAIL [3] 2.1(b): \\leanref{ExampleLib.nowhere} is not a declaration",
                     "FAIL [4] 2.1(b): key declaration ExampleLib.cover_univ is not \\leanref'd here",
                     "FAIL [4] 2.2(b): key declaration ExampleLib.chain_long belongs to 2.2(a)",
                     "FAIL [5] 1.2(a): \\hypOpen is missing: ExampleLib.glue_theorem binds IsOpen",
                     "FAIL [5] 1.2(b): \\hypOpen is missing: ExampleLib.glue_surjective binds",
                     "FAIL [6] 1.2: \\fcref{Theorem 4.1} should be \\fcref*{Theorem 4.1}"):
            with self.subTest(line=line):
                self.assertIn(line, out.stdout)


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
