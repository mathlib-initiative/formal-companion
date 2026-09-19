r"""Regression tests for adapter detection, the adapter registry and blueprints.

Every case here failed before the change it guards:

* the tag spelling was hard-coded.  A companion that writes the book label bare
  at the start of the docstring -- the second convention ``adapters.itemtag``
  itself names -- scored 0 in detection, and a hand-forced
  ``--adapter docstring_tags`` wrote an adapter.yaml demanding backticks, so
  ``cf extract`` found one tag in two thousand docstrings;
* "cannot tell what kind of companion this is" was the whole of the refusal: not
  what was looked for, not what was found, not which convention was nearly
  matched, and not the override that would settle it;
* the adapter-kind vocabulary was written out by hand in four places, and the
  two the tests did not pin had drifted: a repo carrying a
  ``source-formalization-ledger.json`` was auto-classified as the unimplemented
  ``ledger_nodes`` and refused, even when ``docstring_tags`` would have read it;
* ``Adapter.detect`` existed, was documented in CLAUDE.md with the wrong
  signature, and was called by nothing;
* the most common item-to-declaration convention in the Lean ecosystem --
  ``\lean{}`` / ``\leanok`` inside a leanblueprint manuscript -- had no adapter,
  and ``blueprint`` appeared in the tree only as noise to exclude.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cf as cf_main                                           # noqa: E402
from adapters import api as adapters_api                       # noqa: E402
from adapters.base import validate_rows                        # noqa: E402
from adapters.blueprint_tex import BlueprintTexAdapter, newtheorems, order_files  # noqa: E402
from adapters.docstring_tags import DocstringTagsAdapter       # noqa: E402
from cflib import discovery, vocab                             # noqa: E402
from cflib.errors import CfError                               # noqa: E402
from cflib.workspace import Workspace                          # noqa: E402

SHA = "a" * 64


def tmpdir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir=os.environ.get("CF_TEST_TMP") or None))


# ---------------------------------------------------------------------------
# generality#3 -- both spellings of the docstring tag
# ---------------------------------------------------------------------------

#: The shape of the bare-label convention, measured on the public repo
#: ``adapters.itemtag`` names as the bare case (109 .lean files: 102 carry a
#: docstring, 86 of those open one with a bare book label, and exactly ONE
#: docstring in the whole repo carries a backticked tag).  The fixture
#: reproduces the ratio rather than the source, so the score it produces is the
#: score that repo produces.
BARE_DOCUMENTED = 102
BARE_TAGGED = 86
BARE_UNDOCUMENTED = 7


def _bare_label_repo(root: Path) -> Path:
    """A companion that writes the label bare at the start of the docstring."""
    repo = root / "bare"
    (repo / "Src").mkdir(parents=True)
    for i in range(BARE_TAGGED):
        (repo / "Src" / f"Tagged{i:03d}.lean").write_text(
            f"/-- Definition 1.{i // 20 + 1}.{i % 20 + 1}. A widget of kind {i}. -/\n"
            f"theorem widget{i} : True := trivial\n", encoding="utf-8")
    for i in range(BARE_DOCUMENTED - BARE_TAGGED):
        (repo / "Src" / f"Plain{i:03d}.lean").write_text(
            f"/-- A helper with no book label. -/\ntheorem helper{i} : True := trivial\n",
            encoding="utf-8")
    for i in range(BARE_UNDOCUMENTED):
        (repo / "Src" / f"Undoc{i:03d}.lean").write_text(
            f"theorem undoc{i} : True := trivial\n", encoding="utf-8")
    return repo


def _backticked_repo(root: Path) -> Path:
    """A companion that wraps the label in backticks (the stricter spelling)."""
    repo = root / "ticked"
    (repo / "Src").mkdir(parents=True)
    for i in range(30):
        (repo / "Src" / f"S{i:03d}.lean").write_text(
            f"/-- `Lemma 1.2.{i + 1}` (book p. {20 + i}) -/\n"
            f"theorem lem{i} : True := trivial\n", encoding="utf-8")
    return repo


class TestBothTagSpellingsAreScored(unittest.TestCase):
    """The spelling is a fact about the repo, not a default (generality#3)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tmpdir("cf-rf-adapters-tags-")
        cls.bare = _bare_label_repo(cls.root)
        cls.ticked = _backticked_repo(cls.root)

    def test_a_bare_label_repo_is_detected_and_records_the_spelling(self):
        score, evidence = DocstringTagsAdapter.detect(self.bare)
        self.assertGreaterEqual(score, discovery.MIN_ADAPTER_CONFIDENCE)
        self.assertIs(evidence["require_backticks"], False)
        self.assertEqual(evidence["tagged_backticked"], 0)
        self.assertEqual(evidence["tagged_bare"], BARE_TAGGED)
        self.assertEqual(evidence["documented"], BARE_DOCUMENTED)

    def test_the_bare_score_is_the_measured_one(self):
        """0.6 + 0.35 * 86/102 = 0.8951 -- comfortably over the 0.5 floor."""
        score, _evidence = DocstringTagsAdapter.detect(self.bare)
        self.assertEqual(score, 0.8951)
        kind, detected, _ = discovery.detect_adapter(self.bare)
        self.assertEqual(kind, "docstring_tags")
        self.assertEqual(detected, 0.8951)

    def test_a_handful_of_bare_labels_is_not_a_convention(self):
        """Scoring the bare spelling must not onboard a repo that merely mentions items.

        A bare label has no delimiter, so a prose docstring opening "Theorem 1.2
        of [X] says" matches it.  Without the habit floor, a blueprint research
        project with five such docstrings in 161 was auto-classified as a
        docstring-tagged companion at 0.61 -- a worse answer than a refusal.
        """
        from adapters.docstring_tags import MIN_BARE_FRACTION

        root = tmpdir("cf-rf-adapters-fewbare-")
        repo = root / "mentions"
        (repo / "Src").mkdir(parents=True)
        for i in range(60):
            (repo / "Src" / f"P{i:03d}.lean").write_text(
                "/-- A helper with no book label. -/\ntheorem h : True := trivial\n",
                encoding="utf-8")
        for i in range(2):
            (repo / "Src" / f"M{i:03d}.lean").write_text(
                f"/-- Theorem 1.{i + 1} of the paper motivates this. -/\n"
                f"theorem m{i} : True := trivial\n", encoding="utf-8")
        _files, _sampled, backticked, bare, documented = DocstringTagsAdapter._measure(repo)
        self.assertEqual((backticked, bare), (0, 2))
        self.assertLess(bare / documented, MIN_BARE_FRACTION)
        self.assertEqual(DocstringTagsAdapter.detect(repo), (0.0, {}))
        with self.assertRaises(CfError):
            discovery.detect_adapter(repo)

    def test_a_backticked_repo_keeps_the_strict_spelling(self):
        score, evidence = DocstringTagsAdapter.detect(self.ticked)
        self.assertGreaterEqual(score, discovery.MIN_ADAPTER_CONFIDENCE)
        self.assertIs(evidence["require_backticks"], True)
        self.assertEqual(evidence["tagged_backticked"], evidence["tagged_bare"])

    def test_a_bare_repo_extracts_rows_with_the_detected_spelling(self):
        """The point of the flag: the same tree yields 0 rows or 86 of them."""
        base = {"adapter": "docstring_tags", "name": "bare", "repo": str(self.bare),
                "glob": "Src/*.lean", "sources": {"default": "s"}}
        strict = adapters_api.from_config({**base, "tag": {"require_backticks": True}})
        loose = adapters_api.from_config({**base, "tag": {"require_backticks": False}})
        self.assertEqual(len(list(strict.extract(sources={"s": SHA}))), 0)
        rows = list(loose.extract(sources={"s": SHA}))
        self.assertEqual(len(rows), BARE_TAGGED)
        self.assertEqual(validate_rows(rows), [])

    def test_the_template_carries_the_key_and_the_two_windows(self):
        text = (SKILL / "templates" / "adapter.yaml.example").read_text(encoding="utf-8")
        for key in ("require_backticks: {{ require_backticks }}", "inline_window:",
                    "clause_window:"):
            self.assertIn(key, text)


class TestOnboardWritesTheDetectedSpelling(unittest.TestCase):
    """`cf onboard` writes what it measured into adapter.yaml, both paths."""

    def setUp(self):
        self.root = tmpdir("cf-rf-adapters-onboard-")
        self.ws = self.root / "ws"
        self.assertEqual(cf_main.main(["init", str(self.ws), "--no-git", "-q"]), 0)

    def _adapter_yaml(self, repo: Path, name: str, *extra) -> str:
        from cfcmd import onboard as onboard_cmd

        class Args:
            force = True
            write_policy = "read_only"
            owner = "external"

        kind, _conf, evidence = onboard_cmd._detect(repo, extra[0] if extra else None)
        path = onboard_cmd._write_adapter_yaml(
            Workspace.load(self.ws), name, repo, kind, None, [], "s", Args,
            evidence=evidence)
        return path.read_text(encoding="utf-8")

    def test_a_bare_label_repo_gets_require_backticks_false(self):
        repo = _bare_label_repo(self.root)
        text = self._adapter_yaml(repo, "bare")
        self.assertRegex(text, r"require_backticks:\s*false")

    def test_the_manual_adapter_flag_still_measures_the_spelling(self):
        """`--adapter docstring_tags` settles WHICH convention, not HOW it spells."""
        repo = _bare_label_repo(self.root)
        text = self._adapter_yaml(repo, "bare2", "docstring_tags")
        self.assertRegex(text, r"require_backticks:\s*false")

    def test_a_backticked_repo_gets_require_backticks_true(self):
        repo = _backticked_repo(self.root)
        text = self._adapter_yaml(repo, "ticked")
        self.assertRegex(text, r"require_backticks:\s*true")

    def test_the_written_yaml_is_readable_and_drives_the_adapter(self):
        from cflib.adapters_api import load as load_adapter

        repo = _bare_label_repo(self.root)
        self._adapter_yaml(repo, "bare3")
        adapter = load_adapter(Workspace.load(self.ws).paths.adapter_yaml("bare3"))
        self.assertIs(adapter.get("tag.require_backticks"), False)


# ---------------------------------------------------------------------------
# generality#2 -- a refusal that says what was looked for and what was found
# ---------------------------------------------------------------------------

class TestTheRefusalIsDiagnostic(unittest.TestCase):

    def setUp(self):
        self.root = tmpdir("cf-rf-adapters-report-")

    def test_a_repo_nothing_claims_gets_a_line_per_kind(self):
        repo = self.root / "nothing"
        (repo / "Src").mkdir(parents=True)
        for i in range(25):
            (repo / "Src" / f"F{i}.lean").write_text(
                "/-- A helper. -/\ntheorem f : True := trivial\n", encoding="utf-8")
        lines = discovery.detect_report(repo)
        joined = "\n".join(lines)
        for kind in adapters_api.registry():
            self.assertTrue(any(kind.replace("_", " ") in line for line in lines),
                            f"{kind} has no 'looked for' line:\n{joined}")
        self.assertIn("looked for:", joined)
        self.assertIn("declared in the vocabulary but not implemented", joined)

    def test_the_bare_versus_backticked_counts_are_in_the_report(self):
        """The single highest-value line: it turns the miss into a self-diagnosing one."""
        repo = self.root / "bare-but-unnumbered"
        (repo / "Src").mkdir(parents=True)
        for i in range(25):
            # a bare label the item grammar will keep, but no backticks anywhere
            (repo / "Src" / f"F{i}.lean").write_text(
                f"/-- Definition 1.1.{i + 1}. A widget. -/\ntheorem f{i} : True := trivial\n",
                encoding="utf-8")
        lines = [l for l in DocstringTagsAdapter.explain(repo)]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"\d+ backticked, \d+ bare")

    def test_the_refusal_names_the_report_and_every_flag(self):
        repo = self.root / "empty"
        repo.mkdir()
        with self.assertRaises(CfError) as ctx:
            discovery.detect_adapter(repo)
        message, hint = str(ctx.exception), ctx.exception.hint or ""
        self.assertIn("looked for:", message)
        for kind in adapters_api.registry():
            self.assertIn(f"--adapter {kind}", hint)

    def test_a_weak_best_guess_says_which_kind_and_which_floor(self):
        """A score under the floor is still evidence: name it, and the flag."""
        calls = {}

        class _Weak(DocstringTagsAdapter):
            kind = "docstring_tags"

            @classmethod
            def detect(cls, repo):
                calls["seen"] = True
                return 0.4, {"reason": "a weak guess"}

        original = adapters_api._REGISTRY["docstring_tags"]
        adapters_api._REGISTRY["docstring_tags"] = _Weak
        try:
            with self.assertRaises(CfError) as ctx:
                discovery.detect_adapter(self.root)
        finally:
            adapters_api._REGISTRY["docstring_tags"] = original
        self.assertTrue(calls.get("seen"), "detect_scores did not call the class's detect()")
        self.assertIn("docstring_tags scores only 0.40", str(ctx.exception))
        self.assertIn("--adapter docstring_tags", ctx.exception.hint or "")


# ---------------------------------------------------------------------------
# architecture#6 -- one place per adapter kind, not four
# ---------------------------------------------------------------------------

class TestDetectionIsALoopOverTheRegistry(unittest.TestCase):

    def test_every_kind_detect_scores_can_emit_is_registered(self):
        """The test the review asked for: the two lists cannot drift again."""
        root = tmpdir("cf-rf-adapters-kinds-")
        overlay = root / "overlay"
        (overlay / "pdf-overlay" / "data").mkdir(parents=True)
        (overlay / "pdf-overlay" / "data" / "a.json").write_text("{}", encoding="utf-8")
        (overlay / "pdf-overlay" / "official-source.json").write_text("{}", encoding="utf-8")
        for repo in (root, overlay, _backticked_repo(root), _blueprint_repo(root)):
            with self.subTest(repo=Path(repo).name):
                self.assertLessEqual(set(discovery.detect_scores(Path(repo))),
                                     set(adapters_api.registry()))

    def test_the_implemented_vocabulary_is_the_registry(self):
        self.assertEqual(set(adapters_api.registry()), set(vocab.IMPLEMENTED_ADAPTER_KINDS))
        self.assertTrue(vocab.IMPLEMENTED_ADAPTER_KINDS <= vocab.ADAPTER_KINDS)

    def test_a_ledger_only_repo_is_no_longer_classified_as_an_unimplemented_kind(self):
        """It used to score a flat 0.9 for ledger_nodes, win, and be refused."""
        root = tmpdir("cf-rf-adapters-ledger-")
        repo = root / "ledgeronly"
        repo.mkdir()
        (repo / "source-formalization-ledger.json").write_text("{}", encoding="utf-8")
        self.assertNotIn("ledger_nodes", discovery.detect_scores(repo))
        with self.assertRaises(CfError) as ctx:
            discovery.detect_adapter(repo)
        self.assertIn("ledger_nodes", str(ctx.exception))

    def test_a_ledger_repo_that_docstring_tags_can_read_is_read(self):
        """The papercut: a readable repo refused because of a file beside it."""
        root = tmpdir("cf-rf-adapters-ledger2-")
        repo = _backticked_repo(root)
        (repo / "source-formalization-ledger.json").write_text("{}", encoding="utf-8")
        kind, score, _ = discovery.detect_adapter(repo)
        self.assertEqual(kind, "docstring_tags")
        self.assertGreaterEqual(score, discovery.MIN_ADAPTER_CONFIDENCE)

    def test_the_confidence_bonus_comes_from_the_adapter_class(self):
        """The fourth hand-written table: it gave no bonus for `citation`."""
        from adapters.attribution_table import AttributionTableAdapter

        root = tmpdir("cf-rf-adapters-bonus-")
        repo = root / "cited"
        repo.mkdir()
        (repo / "README.md").write_text(
            "See the errata at https://example.org/errata for this book.\n", encoding="utf-8")
        plain = {c.signal: c.confidence for c in discovery.discover_sources(repo)}
        ranked = {c.signal: c.confidence
                  for c in discovery.discover_sources(repo, adapter="attribution_table")}
        self.assertIn("citation", plain)
        self.assertIn("citation", AttributionTableAdapter.PRIMARY_SIGNALS)
        self.assertAlmostEqual(ranked["citation"], plain["citation"] + 0.05, places=6)

    def test_the_unimplemented_kind_refusal_names_the_runner_up(self):
        from cfcmd.onboard import _check_adapter_implemented

        root = tmpdir("cf-rf-adapters-hint-")
        repo = _backticked_repo(root)
        with self.assertRaises(CfError) as ctx:
            _check_adapter_implemented("ledger_nodes", repo)
        hint = ctx.exception.hint or ""
        self.assertIn("--adapter docstring_tags", hint)
        self.assertIn("onboarding-contract.md", hint)


# ---------------------------------------------------------------------------
# docs-accuracy#4 -- CLAUDE.md section 6 against the real API
# ---------------------------------------------------------------------------

class TestTheAdapterContractIsDocumentedAsItIs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        text = (SKILL / "CLAUDE.md").read_text(encoding="utf-8")
        start = text.index("## 6. Adding an adapter")
        cls.section = text[start:text.index("## 7.", start)]

    def test_the_detect_bullet_has_the_real_signature(self):
        self.assertIn("detect(cls, repo) -> (score, evidence)", self.section)
        self.assertNotIn("detect(repo) -> float", self.section)

    def test_every_method_the_section_names_exists(self):
        from cflib.adapters_api import Adapter as CoreAdapter

        named = set(re.findall(r"`(\w+)\(", self.section))
        for name in sorted(named - {"load", "registry", "register"}):
            with self.subTest(method=name):
                self.assertTrue(hasattr(CoreAdapter, name) or hasattr(adapters_api.Adapter, name),
                                f"CLAUDE.md section 6 names {name}(), which no Adapter has")

    def test_detect_is_no_longer_a_shim_that_nothing_calls(self):
        """The documented hook is the one detection really uses."""
        seen = []

        class _Probe(DocstringTagsAdapter):
            kind = "docstring_tags"

            @classmethod
            def detect(cls, repo):
                seen.append(Path(repo))
                return 0.0, {}

        original = adapters_api._REGISTRY["docstring_tags"]
        adapters_api._REGISTRY["docstring_tags"] = _Probe
        try:
            discovery.detect_scores(Path("/nonexistent-repo-for-the-probe"))
        finally:
            adapters_api._REGISTRY["docstring_tags"] = original
        self.assertEqual(seen, [Path("/nonexistent-repo-for-the-probe")])


# ---------------------------------------------------------------------------
# generality#5 -- the blueprint adapter
# ---------------------------------------------------------------------------

MACROS = r"""\newtheorem{theorem}{Theorem}[chapter]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{definition}[theorem]{Definition}
\newtheorem{corollary}[theorem]{Corollary}
"""

CONTENT = r"""\input{macros/common.tex}
\input{chapters/two.tex}
\input{chapters/one.tex}
"""

CHAPTER_TWO = r"""\chapter{Gadgets}

\begin{theorem}\label{thm:gadget}
  \lean{Gadget.main}
  \uses{lem:widget_unique}
  Every gadget is a widget.
\end{theorem}
\begin{proof}
  Not finished.
\end{proof}
"""

CHAPTER_ONE = r"""\chapter{Widgets}

\begin{definition}[Widget]\label{def:widget}
  \lean{Widget.mk}
  \leanok
  A \emph{widget} is a set with one marked point.
\end{definition}

\begin{lemma}\label{lem:widget_unique}
  \lean{Widget.unique, Widget.unique'}
  \uses{def:widget}
  \leanok
  Every widget has exactly one marked point.
\end{lemma}
\begin{proof}
  \leanok
  Immediate.
\end{proof}

\begin{lemma}\label{lem:unformalised}
  Nobody has formalised this one.
\end{lemma}
"""


def _blueprint_repo(root: Path, *, macros: str = MACROS) -> Path:
    """A synthetic leanblueprint tree: the convention's shape, not anyone's source."""
    repo = root / "blueprintrepo"
    src = repo / "blueprint" / "src"
    (src / "chapters").mkdir(parents=True, exist_ok=True)
    (src / "macros").mkdir(parents=True, exist_ok=True)
    (src / "macros" / "common.tex").write_text(macros, encoding="utf-8")
    (src / "content.tex").write_text(CONTENT, encoding="utf-8")
    (src / "chapters" / "one.tex").write_text(CHAPTER_ONE, encoding="utf-8")
    (src / "chapters" / "two.tex").write_text(CHAPTER_TWO, encoding="utf-8")
    return repo


class TestBlueprintTexAdapter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tmpdir("cf-rf-adapters-bp-")
        cls.repo = _blueprint_repo(cls.root)
        cls.adapter = adapters_api.from_config({
            "adapter": "blueprint_tex", "name": "bp", "repo": str(cls.repo),
            "sources": {"default": "s"}})

    def rows(self, **config):
        adapter = self.adapter if not config else adapters_api.from_config({
            "adapter": "blueprint_tex", "name": "bp", "repo": str(self.repo),
            "sources": {"default": "s"}, **config})
        return list(adapter.extract(sources={"s": SHA}))

    def test_the_kind_is_registered_and_implemented(self):
        self.assertIn("blueprint_tex", adapters_api.registry())
        self.assertIn("blueprint_tex", vocab.IMPLEMENTED_ADAPTER_KINDS)
        self.assertIn("blueprint_tex", vocab.ADAPTER_KINDS)

    def test_it_is_detected_on_the_recursive_glob(self):
        """brownian-motion's macros live in blueprint/src/chapters/, not blueprint/src/."""
        score, evidence = BlueprintTexAdapter.detect(self.repo)
        self.assertGreaterEqual(score, discovery.MIN_ADAPTER_CONFIDENCE)
        self.assertEqual(evidence["with_lean"], 3)
        self.assertEqual(evidence["environments"], 4)
        kind, _score, _ev = discovery.detect_adapter(self.repo)
        self.assertEqual(kind, "blueprint_tex")

    def test_a_blueprint_with_no_lean_macros_scores_nothing(self):
        bare = _blueprint_repo(tmpdir("cf-rf-adapters-bp2-"))
        for path in (bare / "blueprint" / "src").rglob("*.tex"):
            path.write_text(re.sub(r"\\lean\{[^}]*\}", "", path.read_text(encoding="utf-8")),
                            encoding="utf-8")
        self.assertEqual(BlueprintTexAdapter.detect(bare), (0.0, {}))

    def test_it_extracts_real_rows_that_satisfy_the_schema(self):
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(validate_rows(rows), [])
        self.assertEqual(sorted({r["item"] for r in rows}),
                         ["Definition 2.1", "Lemma 2.2", "Theorem 1.1"])
        self.assertEqual(sorted(r["decl"] for r in rows),
                         ["Gadget.main", "Widget.mk", "Widget.unique", "Widget.unique'"])

    def test_the_item_follows_the_master_file_order_not_the_path_order(self):
        """content.tex inputs chapter two first, so Theorem 1.1 is in two.tex."""
        rows = {r["item"]: r for r in self.rows()}
        self.assertTrue(rows["Theorem 1.1"]["file"].endswith("two.tex"))
        self.assertTrue(rows["Definition 2.1"]["file"].endswith("one.tex"))

    def test_the_statement_text_is_the_manuscript_without_the_machinery(self):
        rows = {r["item"]: r for r in self.rows()}
        statement = rows["Definition 2.1"]["statement"]
        for macro in ("\\lean", "\\leanok", "\\label", "\\uses"):
            self.assertNotIn(macro, statement)
        # `\lean` must not be stripped out of `\leanok` and leave "ok" standing
        self.assertNotIn("ok A", statement)
        self.assertIn("marked point", statement)

    def test_leanok_is_the_status_and_a_missing_one_is_a_gap(self):
        rows = {r["item"]: r for r in self.rows()}
        self.assertFalse(rows["Lemma 2.2"]["sorry"])
        self.assertEqual(rows["Lemma 2.2"]["sorry_class"], "none")
        self.assertTrue(rows["Theorem 1.1"]["sorry"])
        self.assertEqual(rows["Theorem 1.1"]["sorry_class"], "gap")
        # a definition has no proof environment: its own \leanok is the status
        self.assertFalse(rows["Definition 2.1"]["sorry"])

    def test_an_unformalised_statement_is_counted_not_invented(self):
        counts = self.adapter.dry_run(sources={"s": SHA})
        self.assertEqual(counts["declarations"], 4)
        self.assertEqual(counts["tagged"], 3)
        self.assertEqual(counts["untagged"], 1)
        self.assertEqual(counts["items"], 3)
        self.assertEqual(counts["uses_edges"], 2)
        self.assertEqual(counts["statement_leanok"], 2)
        self.assertEqual(counts["proof_leanok"], 1)
        self.assertEqual(counts["locator"], "number")

    def test_the_locator_falls_back_to_the_label_and_says_so(self):
        """No \\newtheorem declarations: LaTeX's numbering is not recoverable."""
        repo = _blueprint_repo(tmpdir("cf-rf-adapters-bp3-"), macros="% no declarations here\n")
        adapter = adapters_api.from_config({
            "adapter": "blueprint_tex", "name": "bp", "repo": str(repo),
            "sources": {"default": "s"}})
        counts = adapter.dry_run(sources={"s": SHA})
        self.assertEqual(counts["locator"], "label")
        rows = list(adapter.extract(sources={"s": SHA}))
        self.assertEqual(sorted({r["item"] for r in rows}),
                         ["def:widget", "lem:widget_unique", "thm:gadget"])
        self.assertEqual(validate_rows(rows), [])

    def test_the_locator_can_be_forced_from_adapter_yaml(self):
        rows = self.rows(blueprint={"locator": "label"})
        self.assertIn("thm:gadget", {r["item"] for r in rows})

    def test_a_blueprint_carries_no_page_anchors(self):
        for row in self.rows():
            self.assertIsNone(row["page_anchor"])
            self.assertEqual(row["anchor_source"], "none")

    def test_newtheorem_shares_the_counter_and_the_reset(self):
        table = newtheorems(MACROS)
        self.assertEqual(table["lemma"]["counter"], "theorem")
        self.assertEqual(table["lemma"]["reset"], "chapter")
        self.assertEqual(table["definition"]["display"], "Definition")

    def test_order_files_puts_the_master_s_inputs_first(self):
        src = self.repo / "blueprint" / "src"
        files = sorted(src.rglob("*.tex"))
        ordered = order_files(files, self.repo)
        self.assertEqual([p.name for p in ordered][:3],
                         ["common.tex", "two.tex", "one.tex"])

    def test_a_declaration_name_with_spaces_is_rejected_not_written(self):
        repo = _blueprint_repo(tmpdir("cf-rf-adapters-bp4-"))
        path = repo / "blueprint" / "src" / "chapters" / "two.tex"
        path.write_text(path.read_text(encoding="utf-8").replace(
            r"\lean{Gadget.main}", r"\lean{Gadget main}"), encoding="utf-8")
        adapter = adapters_api.from_config({
            "adapter": "blueprint_tex", "name": "bp", "repo": str(repo),
            "sources": {"default": "s"}})
        counts = adapter.dry_run(sources={"s": SHA})
        self.assertEqual(counts["rejected"], 1)
        self.assertEqual(counts["rejects"][0]["reason"], "not_a_declaration_name")
        self.assertEqual(validate_rows(list(adapter.extract(sources={"s": SHA}))), [])

    def test_onboard_has_defaults_for_the_new_kind(self):
        from cfcmd.onboard import DEFAULTS

        self.assertEqual(set(DEFAULTS), set(vocab.ADAPTER_KINDS))
        self.assertEqual(DEFAULTS["blueprint_tex"]["anchor"], "none")
        self.assertEqual(DEFAULTS["blueprint_tex"]["glob"], "blueprint/**/*.tex")

    def test_the_extract_key_moves_with_the_manuscript(self):
        before = self.adapter.key()
        path = self.repo / "blueprint" / "src" / "chapters" / "one.tex"
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "\n% a later edit\n", encoding="utf-8")
            self.assertNotEqual(self.adapter.key(), before)
        finally:
            path.write_text(original, encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
