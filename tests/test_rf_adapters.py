r"""Regression tests for adapter detection, the adapter registry and blueprints.

Most cases here fail against the branch point; four pin an invariant that already
held (the implemented vocabulary against the registry, every kind detection can
emit being registered, every method CLAUDE.md names existing, and a bare-label
repo extracting rows once the flag is set by hand) and are kept as invariants
rather than advertised as guards.  The eighteen blueprint cases cannot run at the
branch point at all: the module does not exist there.

What the rest guard:

* the tag spelling was hard-coded.  A companion that writes the book label bare
  at the start of the docstring -- the second convention ``adapters.itemtag``
  itself names -- scored 0 in detection, and a hand-forced
  ``--adapter docstring_tags`` wrote an adapter.yaml demanding backticks, so
  ``cf extract`` found one tag in two thousand docstrings;
* "cannot tell what kind of companion this is" was the whole of the refusal: not
  what was looked for, not what was found, not which convention was nearly
  matched, and not the override that would settle it -- and a detector that
  CRASHED was recorded and then filtered out of the very report that exists to
  say why nothing matched;
* the adapter-kind vocabulary was written out by hand in four places, and the
  two the tests did not pin had drifted: a repo carrying a
  ``source-formalization-ledger.json`` was auto-classified as the unimplemented
  ``ledger_nodes`` and refused, even when ``docstring_tags`` would have read it;
* ``Adapter.detect`` existed, was documented in CLAUDE.md with the wrong
  signature, and was called by nothing;
* making detection a loop over the registry broke it behind the stand-in
  ``adapters`` module several test classes onboard with, and the suite stayed
  green only by import order;
* the most common item-to-declaration convention in the Lean ecosystem --
  ``\lean{}`` / ``\leanok`` inside a leanblueprint manuscript -- had no adapter,
  and ``blueprint`` appeared in the tree only as noise to exclude.
"""
from __future__ import annotations

# The first import, before anything reads HOME: it pins HOME at a temporary
# directory for `unittest discover`, which loads no conftest.py.  The second
# spelling is for `python3 -m unittest tests.<module>`.  See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import os
import re
import sys
import tempfile
import types
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


def _partly_tagged_repo(root: Path, name: str = "partly") -> Path:
    """Half its documented files tagged: 0.6 + 0.35 * 20/40 = 0.775.

    Deliberately BELOW the flat 0.9 the unimplemented ``ledger_nodes`` used to
    score, because that is the only shape in which the papercut reproduces -- a
    fully tagged tree already beat 0.9 and was never refused.
    """
    repo = root / name
    (repo / "Src").mkdir(parents=True)
    for i in range(20):
        (repo / "Src" / f"S{i:03d}.lean").write_text(
            f"/-- `Lemma 1.2.{i + 1}` (book p. {20 + i}) -/\n"
            f"theorem lem{i} : True := trivial\n", encoding="utf-8")
    for i in range(20):
        (repo / "Src" / f"P{i:03d}.lean").write_text(
            f"/-- A helper with no book label. -/\ntheorem helper{i} : True := trivial\n",
            encoding="utf-8")
    return repo


#: the score ``_partly_tagged_repo`` must produce, and the flat score the
#: unimplemented kind used to win with
PARTLY_TAGGED_SCORE = 0.775
OLD_LEDGER_NODES_SCORE = 0.9


def _few_bare_labels_repo(root: Path, name: str = "mentions") -> Path:
    """A repo whose bare labels are prose mentions, not a convention (2 of 62)."""
    repo = root / name
    (repo / "Src").mkdir(parents=True)
    for i in range(60):
        (repo / "Src" / f"P{i:03d}.lean").write_text(
            "/-- A helper with no book label. -/\ntheorem h : True := trivial\n",
            encoding="utf-8")
    for i in range(2):
        (repo / "Src" / f"M{i:03d}.lean").write_text(
            f"/-- Theorem 1.{i + 1} of the paper motivates this. -/\n"
            f"theorem m{i} : True := trivial\n", encoding="utf-8")
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

        repo = _few_bare_labels_repo(tmpdir("cf-rf-adapters-fewbare-"))
        _files, _sampled, backticked, bare, documented = DocstringTagsAdapter._measure(repo)
        self.assertEqual((backticked, bare), (0, 2))
        self.assertLess(bare / documented, MIN_BARE_FRACTION)
        self.assertEqual(DocstringTagsAdapter.detect(repo), (0.0, {}))
        with self.assertRaises(CfError):
            discovery.detect_adapter(repo)

    def test_a_sub_floor_repo_is_told_the_edit_is_by_hand(self):
        """The refusal has to describe the file `--adapter` really writes.

        Below the habit floor the measurement KEEPS the backticked spelling, so
        `cf onboard --adapter docstring_tags` writes ``require_backticks: true``.
        A line reading "set tag.require_backticks: false in adapter.yaml" read as
        a promise that the tool would write false, and the user found true in the
        file it had just generated.
        """
        line = DocstringTagsAdapter.explain(
            _few_bare_labels_repo(tmpdir("cf-rf-adapters-subfloor-")))[0]
        self.assertIn("writes tag.require_backticks: true", line)
        self.assertIn("edit it to false in the generated adapter.yaml", line)

    def test_a_repo_whose_habit_is_bare_is_not_told_to_edit_anything(self):
        """The same line, the other way: here the tool writes false by itself."""
        line = DocstringTagsAdapter.explain(self.bare)[0]
        self.assertIn("writes tag.require_backticks: false", line)
        self.assertNotIn("edit it", line)

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

    def test_a_sub_floor_repo_gets_true_which_is_what_the_refusal_said(self):
        """The generated file and the refusal line agree, so the hand edit is expected."""
        repo = _few_bare_labels_repo(self.root)
        text = self._adapter_yaml(repo, "subfloor", "docstring_tags")
        self.assertRegex(text, r"require_backticks:\s*true")
        self.assertIn("edit it to false in the generated adapter.yaml",
                      DocstringTagsAdapter.explain(repo)[0])

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

    def test_a_detector_that_raised_is_not_swallowed(self):
        """A crashed detector is invisible in the report that exists to say why.

        ``detect_all`` records the exception with score 0, and a report filtering
        on ``score > 0`` printed the ordinary "looked for" lines and never the
        word the user needs: the repo was called unrecognisable while the reason
        it could not be measured went unmentioned.
        """
        class _Explodes(DocstringTagsAdapter):
            kind = "docstring_tags"

            @classmethod
            def detect(cls, repo):
                raise RuntimeError("detector exploded")

        original = adapters_api._REGISTRY["docstring_tags"]
        adapters_api._REGISTRY["docstring_tags"] = _Explodes
        try:
            report = "\n".join(discovery.detect_report(_backticked_repo(self.root)))
        finally:
            adapters_api._REGISTRY["docstring_tags"] = original
        self.assertIn("failed:", report)
        self.assertIn("docstring_tags", report)
        self.assertIn("RuntimeError: detector exploded", report)


# ---------------------------------------------------------------------------
# architecture#6 -- one place per adapter kind, not four
# ---------------------------------------------------------------------------

class TestDetectionSurvivesAStandInAdaptersModule(unittest.TestCase):
    """Detection is core behaviour, so it must work behind the suite's stand-in.

    Several test classes onboard a repo without a real parser by REPLACING the
    top-level ``adapters`` module with a stand-in exposing only ``load()``.  Once
    ``detect_scores`` became a loop over ``adapters.api.registry()``, that
    stand-in broke detection outright -- ``registry()`` imports the shipped
    adapter modules, and a stand-in has no ``__path__`` -- so every such class
    died with ``ImportError: cannot import name 'attribution_table' from
    'adapters'``.  The suite looked green only because another test file had
    already left the submodules in ``sys.modules``: green by import order.
    """

    def test_detect_scores_reads_the_real_package_beside_the_stand_in(self):
        repo = _backticked_repo(tmpdir("cf-rf-adapters-standin-"))
        saved = {name: mod for name, mod in sys.modules.items()
                 if name == "adapters" or name.startswith("adapters.")}
        stand_in = types.ModuleType("adapters")
        stand_in.load = lambda *args, **kwargs: None      # all the stand-in offers
        for name in saved:
            del sys.modules[name]
        sys.modules["adapters"] = stand_in
        try:
            scores = discovery.detect_scores(repo)
            report = "\n".join(discovery.detect_report(repo))
        finally:
            for name in [n for n in sys.modules
                         if n == "adapters" or n.startswith("adapters.")]:
                del sys.modules[name]
            sys.modules.update(saved)
        self.assertIs(sys.modules["adapters"], saved["adapters"])
        self.assertIn("docstring_tags", scores)
        self.assertIn("looked for:", report)


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
        """The papercut: a readable repo refused because of a file beside it.

        The fixture is only half tagged on purpose.  A fully tagged tree scores
        0.95 and already beat the flat 0.9 of the unimplemented ``ledger_nodes``,
        so it never reproduced the refusal; 20 of 40 documented files scores
        0.775, which the flat 0.9 used to win against on the maximum.
        """
        root = tmpdir("cf-rf-adapters-ledger2-")
        repo = _partly_tagged_repo(root)
        (repo / "source-formalization-ledger.json").write_text("{}", encoding="utf-8")
        scores = discovery.detect_scores(repo)
        self.assertEqual(scores["docstring_tags"][0], PARTLY_TAGGED_SCORE)
        self.assertLess(PARTLY_TAGGED_SCORE, OLD_LEDGER_NODES_SCORE)
        kind, score, _ = discovery.detect_adapter(repo)
        self.assertEqual((kind, score), ("docstring_tags", PARTLY_TAGGED_SCORE))
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

    def test_an_escaped_percent_does_not_truncate_the_statement(self):
        r"""``\%`` is the per-cent sign; only a bare ``%`` starts a comment.

        Stripping from an escaped percent cut the statement at the first
        "50\% of", and `statement` is what the quote gate quotes and a
        faithfulness finding compares against the Lean -- so the loss shows up as
        a wrong audit rather than as a failure.  No blueprint to hand uses
        ``\%``, which is why this is a test and not luck.
        """
        repo = _blueprint_repo(tmpdir("cf-rf-adapters-bp5-"))
        path = repo / "blueprint" / "src" / "chapters" / "two.tex"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "Every gadget is a widget.",
            "At least 50\\% of gadgets are widgets, and the rest are not.\n"
            "  % a note to the author, never to the reader"), encoding="utf-8")
        adapter = adapters_api.from_config({
            "adapter": "blueprint_tex", "name": "bp", "repo": str(repo),
            "sources": {"default": "s"}})
        statement = {r["item"]: r for r in adapter.extract(sources={"s": SHA})}[
            "Theorem 1.1"]["statement"]
        self.assertIn("50\\%", statement)
        self.assertIn("the rest are not", statement)
        self.assertNotIn("a note to the author", statement)

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
        self.assertEqual(counts["statements"], 4)
        self.assertEqual(counts["tagged"], 3)
        self.assertEqual(counts["untagged"], 1)
        self.assertEqual(counts["items"], 3)
        self.assertEqual(counts["uses_edges"], 2)
        self.assertEqual(counts["statement_leanok"], 2)
        self.assertEqual(counts["proof_leanok"], 1)
        self.assertEqual(counts["locator"], "number")

    def test_the_two_counters_do_not_share_one_key(self):
        """`declarations` means the same thing in every kind's dry_run.

        It used to be this kind's count of statement ENVIRONMENTS while
        docstring_tags used it for Lean declarations found, and both are written
        into state.json -- so whoever compared two companions' state.json was
        comparing two different measurements under one name.
        """
        counts = self.adapter.dry_run(sources={"s": SHA})
        rows = self.rows()
        self.assertEqual(counts["statements"], counts["tagged"] + counts["untagged"])
        self.assertEqual(counts["declarations"], len({r["decl"] for r in rows}))
        from cflib.adapters_api import DRY_RUN_REQUIRED_KEYS

        for key in DRY_RUN_REQUIRED_KEYS:
            self.assertIn(key, counts)

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
        ordered = order_files(files)
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


# ---------------------------------------------------------------------------
# coverage#1 -- the attribution table against the Lean sources it describes
# ---------------------------------------------------------------------------

#: A companion whose corpus is built from an EXPORT can be short of the
#: repository, and nothing downstream could see it.  Against a real
#: attribution-table repo at one pinned commit: 19 NAMED declarations sit inside
#: a `Companions` region -- 8 under `section Companions`, 11 under `namespace
#: Companions`, and a first check that read only sections missed the larger
#: half -- and all 19 are absent from the table; 4 declarations name a book item
#: in a docstring and 3 of the 4 are absent; one of those three is a public,
#: sorry-free theorem which an audit worker, grepping the corpus and finding
#: nothing, reported as absent from the repository that contains it.  A second
#: worker did the same for a cardinality bound stated under a `namespace
#: Companions` header that names its item only in prose.
#:
#: The fixture is that shape in miniature: a table naming one declaration, and a
#: Lean tree carrying five more that claim the book in one of the ways this
#: convention exposes -- a docstring item, a `section Companions`, a `namespace
#: Companions` -- plus a `.lake` copy of "Mathlib" that the scan must never walk,
#: and an anonymous `example` inside the section that it must never claim (an
#: export names those by curated slug, not by position, so there is no name to
#: compare and every one of them would report as an omission).

TABLE_DECL = "Widget.Basic.gadget_of_bounded"
#: omitted, docstring names its item, NOT in a section -> unioned
DOC_ONLY_DECL = "Widget.Basic.gadget_indicator_of_le"
#: omitted, in `section Companions` AND names its item -> unioned
SECTION_AND_DOC_DECL = "Widget.Basic.gadget_linear_image"
#: omitted, in `section Companions`, names no item -> reported, never unioned
SECTION_ONLY_DECL = "Widget.Basic.gadgetFirstFourVertices"
#: omitted, in `namespace Companions`, names no item -> reported, never unioned;
#: its header names the item only in prose, exactly as the real repo does
NAMESPACE_ONLY_DECL = "Widget.Basic.Companions.borel_card_le_continuum"
#: what a bare `**/*.lean` would drag in
LAKE_DECL = "Mathlib.Order.le_of_bogus"


def _attribution_repo(root: Path) -> Path:
    """A minimal attribution_table companion: one table row, four Lean claims."""
    import json

    repo = root / "widget"
    (repo / "pdf-overlay" / "data").mkdir(parents=True)
    (repo / "Widget").mkdir(parents=True)
    (repo / ".lake" / "packages" / "mathlib" / "Mathlib").mkdir(parents=True)

    (repo / "pdf-overlay" / "official-source.json").write_text(
        json.dumps({"title": "A Book", "sha256": "b" * 64}), encoding="utf-8")
    (repo / "pdf-overlay" / "data" / "geometry-batch-a.json").write_text(json.dumps({
        "items": [
            {"label": "Definition 1.1.1", "regions": [{"region_id": "r1", "page": 21}]},
            # the page the cross-check hands a unioned row: the overlay measured
            # this item even though the table attributes no declaration to it.
            {"label": "Exercise 1.2.21", "regions": [{"region_id": "r2", "page": 41}]},
        ]}), encoding="utf-8")
    (repo / "pdf-overlay" / "data" / "target-refinement.json").write_text(json.dumps({
        "entries": [{"region_id": "r1", "formalization_ids": [TABLE_DECL],
                     "key_formalization_ids": [TABLE_DECL]}]}), encoding="utf-8")
    (repo / "pdf-overlay" / "data" / "declaration-snippets.json").write_text(json.dumps({
        "snippets": {TABLE_DECL: {"file": "Widget/Basic.lean", "line": 6,
                                  "text": "theorem gadget_of_bounded : True := trivial"}}}),
        encoding="utf-8")
    (repo / "source-formalization-ledger.json").write_text(json.dumps({
        "formalization_nodes": [{"id": TABLE_DECL, "kind": "theorem"}]}), encoding="utf-8")

    (repo / "Widget" / "Basic.lean").write_text(
        "namespace Widget.Basic\n"
        "\n"
        "/-- `Definition 1.1.1`, the elementary gadget. -/\n"
        "theorem gadget_of_bounded : True := trivial\n"
        "\n"
        "/-- Exercise 1.1.21(3), integral half, for every interval as in\n"
        "Definition 1.1.1: the gadget of an indicator is its measure. -/\n"
        "theorem gadget_indicator_of_le : True := trivial\n"
        "\n"
        "section Companions\n"
        "\n"
        "/-- A sanity check with no book item at all. -/\n"
        "example : True := trivial\n"
        "\n"
        "/-- Exercise 1.2.21, measure half: the gadget of a linear image. -/\n"
        "theorem gadget_linear_image : True := trivial\n"
        "\n"
        "/-- The first four vertices, as a worked constant. -/\n"
        "private def gadgetFirstFourVertices : Nat := 4\n"
        "\n"
        "end Companions\n"
        "\n"
        "/-- No book item here either. -/\n"
        "theorem gadget_helper : True := trivial\n"
        "\n"
        "namespace Companions\n"
        "\n"
        "/-! ## Companion 5: the gadget hierarchy and cardinality -/\n"
        "\n"
        "/-- Every gadget family has at most continuum many members. -/\n"
        "theorem borel_card_le_continuum (d : Nat) : True := trivial\n"
        "\n"
        "end Companions\n"
        "\n"
        "end Widget.Basic\n", encoding="utf-8")
    # the compiler's copy of the library, which is not this companion
    (repo / ".lake" / "packages" / "mathlib" / "Mathlib" / "Order.lean").write_text(
        "namespace Mathlib.Order\n"
        "\n"
        "section Companions\n"
        "\n"
        "/-- Theorem 1.1.1 of somebody else's book. -/\n"
        "theorem le_of_bogus : True := trivial\n"
        "\n"
        "end Companions\n"
        "\n"
        "end Mathlib.Order\n", encoding="utf-8")
    return repo


def _attribution_adapter(repo: Path, **config):
    return adapters_api.from_config({
        "adapter": "attribution_table", "name": "widget", "repo": str(repo),
        "page_offset": 16, "sources": {"default": "s"}, **config})


class TestTheTableIsCrossCheckedAgainstTheLean(unittest.TestCase):
    """An export that omits a declaration the repository carries is reported."""

    @classmethod
    def setUpClass(cls):
        cls.repo = _attribution_repo(tmpdir("cf-rf-adapters-xcheck-"))
        cls.adapter = _attribution_adapter(cls.repo)
        cls.report = cls.adapter.dry_run(sources={"s": SHA})
        cls.scan = cls.report["lean_scan"]
        cls.rows = list(cls.adapter.extract(sources={"s": SHA}))

    def omissions(self) -> dict:
        return {hit["decl"]: hit for hit in self.scan["omissions"]}

    # -- the scan itself ---------------------------------------------------
    def test_the_omissions_are_named_one_by_one(self):
        self.assertEqual(sorted(self.omissions()),
                         sorted([DOC_ONLY_DECL, SECTION_AND_DOC_DECL, SECTION_ONLY_DECL,
                                 NAMESPACE_ONLY_DECL]))
        self.assertEqual(self.scan["omitted"], 4)
        self.assertEqual(self.scan["in_table"], 1)

    def test_a_namespace_region_counts_exactly_like_a_section(self):
        """The real repo spells the same block both ways, 8 files vs 6.

        A check that read only `section Companions` missed 11 of 19 -- the whole
        of the file whose Companions region a worker then reported as not
        containing a cardinality bound it states for every dimension.
        """
        hit = self.omissions()[NAMESPACE_ONLY_DECL]
        self.assertEqual(hit["signals"], ["namespace Companions"])
        self.assertFalse(hit["unioned"])

    def test_each_omission_says_which_signal_claimed_it(self):
        hits = self.omissions()
        self.assertEqual(hits[DOC_ONLY_DECL]["signals"],
                         ["docstring names Exercise 1.1.21(3)"])
        self.assertEqual(hits[SECTION_AND_DOC_DECL]["signals"],
                         ["docstring names Exercise 1.2.21", "section Companions"])
        self.assertEqual(hits[SECTION_ONLY_DECL]["signals"], ["section Companions"])

    def test_each_omission_carries_its_file_and_line(self):
        for hit in self.scan["omissions"]:
            self.assertEqual(hit["file"], "Widget/Basic.lean")
            self.assertGreater(hit["line"], 0)

    def test_a_declaration_the_table_has_is_not_an_omission(self):
        self.assertNotIn(TABLE_DECL, self.omissions())

    def test_a_declaration_claiming_nothing_is_not_an_omission(self):
        """`gadget_helper` is in neither signal, so the table is right to omit it."""
        self.assertNotIn("Widget.Basic.gadget_helper", self.omissions())

    def test_the_lake_tree_is_never_walked(self):
        """A bare `**/*.lean` over a built Lake project reads Mathlib.

        Its `section Companions` would report every declaration of somebody
        else's library as one this companion's table omits.
        """
        self.assertNotIn(LAKE_DECL, self.omissions())
        read = {str(p) for p in self.adapter.lean_files(self.repo)}
        self.assertEqual(read, {str(self.repo / "Widget" / "Basic.lean")})

    def test_an_anonymous_example_in_the_section_is_counted_not_claimed(self):
        self.assertEqual(self.scan["anonymous_claims"], 1)
        self.assertFalse([d for d in self.omissions() if d.startswith("example@")])

    # -- the policy --------------------------------------------------------
    def test_union_publishes_the_omissions_that_name_an_item(self):
        scanned = {r["decl"]: r for r in self.rows if r["provenance"] == "lean_scan"}
        self.assertEqual(sorted(scanned), sorted([DOC_ONLY_DECL, SECTION_AND_DOC_DECL]))
        self.assertEqual(self.scan["unioned"], 2)

    def test_the_table_rows_are_stamped_table_and_are_not_disturbed(self):
        table = [r for r in self.rows if r["provenance"] == "table"]
        self.assertEqual([r["decl"] for r in table], [TABLE_DECL])
        self.assertEqual(table[0]["item"], "Definition 1.1.1")
        self.assertTrue(table[0]["key"])

    def test_the_unioned_row_carries_the_item_its_docstring_names(self):
        row = next(r for r in self.rows if r["decl"] == SECTION_AND_DOC_DECL)
        self.assertEqual(row["item"], "Exercise 1.2.21")
        self.assertEqual(row["item_kind"], "Exercise")
        self.assertEqual((row["file"], row["line"]), ("Widget/Basic.lean", 16))
        # the table decides what is KEY for an item, and it never saw this row
        self.assertFalse(row["key"])

    def test_the_leading_item_wins_over_a_cross_reference(self):
        """"Exercise 1.1.21(3) ... as in Definition 1.1.1" claims the exercise."""
        row = next(r for r in self.rows if r["decl"] == DOC_ONLY_DECL)
        self.assertEqual((row["item"], row["sub"]), ("Exercise 1.1.21", "(3)"))

    def test_a_unioned_row_takes_its_page_from_the_overlay_or_none(self):
        """The repo's own geometry, converted with the offset -- never a guess."""
        linear = next(r for r in self.rows if r["decl"] == SECTION_AND_DOC_DECL)
        self.assertEqual((linear["page_anchor"], linear["anchor_source"]), (25, "own"))
        # the overlay never measured Exercise 1.1.21, so this row has no anchor
        indicator = next(r for r in self.rows if r["decl"] == DOC_ONLY_DECL)
        self.assertEqual((indicator["page_anchor"], indicator["anchor_source"]),
                         (None, "none"))

    def test_an_omission_naming_no_item_is_reported_and_never_keyed(self):
        """An unparseable/absent id must never become a key (CLAUDE.md S4)."""
        self.assertEqual(self.scan["unattributed"], 2)
        for decl in (SECTION_ONLY_DECL, NAMESPACE_ONLY_DECL):
            self.assertFalse(self.omissions()[decl]["unioned"])
            self.assertNotIn(decl, {r["decl"] for r in self.rows})

    def test_the_unattributed_go_to_their_own_file_s_rows(self):
        """They cannot be unioned, so they are handed over as a list instead.

        A corpus search by item cannot find these by construction; the only way
        a worker meets them is by being given the file, which `cf plan-run`
        stages beside the tables.  `repo` is on every row so a merged read stays
        attributable.
        """
        from cflib.adapters_api import unattributed_rows

        rows = unattributed_rows("widget", self.report)
        self.assertEqual(sorted(r["decl"] for r in rows),
                         sorted([SECTION_ONLY_DECL, NAMESPACE_ONLY_DECL]))
        for row in rows:
            self.assertEqual(row["repo"], "widget")
            self.assertEqual(row["file"], "Widget/Basic.lean")
            self.assertTrue(row["signals"])
            self.assertFalse(row["unioned"])
        # and the unioned ones are NOT in it: they are in the table
        self.assertNotIn(SECTION_AND_DOC_DECL, {r["decl"] for r in rows})

    def test_report_policy_warns_without_publishing(self):
        adapter = _attribution_adapter(self.repo, lean_scan={"policy": "report"})
        scan = adapter.dry_run(sources={"s": SHA})["lean_scan"]
        self.assertEqual(scan["omitted"], 4)
        self.assertEqual(scan["unioned"], 0)
        rows = list(adapter.extract(sources={"s": SHA}))
        self.assertEqual({r["provenance"] for r in rows}, {"table"})

    def test_off_is_the_only_way_to_silence_it_and_it_is_written_down(self):
        adapter = _attribution_adapter(self.repo, lean_scan={"policy": "off"})
        scan = adapter.dry_run(sources={"s": SHA})["lean_scan"]
        self.assertEqual(scan["policy"], "off")
        self.assertEqual(scan.get("omissions"), None)

    def test_an_unknown_policy_is_refused_at_load(self):
        with self.assertRaises(CfError) as ctx:
            _attribution_adapter(self.repo, lean_scan={"policy": "ignore"})
        self.assertIn("union", ctx.exception.hint or "")

    def test_the_regions_that_count_are_configuration(self):
        """`Companions` is measure_theory's word, not a constant of the model."""
        adapter = _attribution_adapter(self.repo, lean_scan={"regions": ["Addenda"]})
        scan = adapter.dry_run(sources={"s": SHA})["lean_scan"]
        self.assertEqual(scan["regions"], ["Addenda"])
        self.assertEqual(sorted(h["decl"] for h in scan["omissions"]),
                         sorted([DOC_ONLY_DECL, SECTION_AND_DOC_DECL]))
        # the spelling a file written before the namespace form was found used
        legacy = _attribution_adapter(self.repo, lean_scan={"sections": ["Addenda"]})
        self.assertEqual(legacy.claim_regions(), ("Addenda",))

    # -- what the rest of the pipeline sees --------------------------------
    def test_every_row_still_validates_against_schema_v2(self):
        self.assertEqual(validate_rows(self.rows, require_source_sha=False), [])

    def test_the_warning_names_every_omitted_declaration(self):
        from cflib.adapters_api import cross_check_warning

        header, lines = cross_check_warning("widget", self.report)
        self.assertIn("omits 4 declaration(s)", header)
        body = "\n".join(lines)
        for decl in (DOC_ONLY_DECL, SECTION_AND_DOC_DECL, SECTION_ONLY_DECL,
                     NAMESPACE_ONLY_DECL):
            self.assertIn(decl, body)
        self.assertIn("unattributed.jsonl", body)
        self.assertIn("Widget/Basic.lean:", body)
        # and it says what the omission MEANS, not only that there is one
        self.assertIn("defect in the companion's own attribution table", body)
        self.assertIn("absence from this corpus as absence from the repository", body)

    def test_the_state_record_is_the_same_shape_the_warning_reads(self):
        """`cf extract` replays it on an up-to-date table from state.json.

        The no-op path does not re-read the repo -- the omissions cannot have
        moved while the key has not -- so it warns from what the last extraction
        recorded, and the recorded shape has to be the one the formatter reads.
        """
        from cflib.adapters_api import CROSS_CHECK_KEY, cross_check_warning

        recorded = {CROSS_CHECK_KEY: self.report[CROSS_CHECK_KEY]}
        header, _lines = cross_check_warning("widget", recorded)
        self.assertIn("omits 4 declaration(s)", header)

    def test_extract_writes_the_unattributed_file_and_removes_a_stale_one(self):
        """`cf extract` and `cf onboard` leave the same file, through one function."""
        from cfcmd.extract import write_unattributed

        ws_root = tmpdir("cf-rf-adapters-unattr-") / "ws"
        self.assertEqual(cf_main.main(["init", str(ws_root), "--no-git", "-q"]), 0)
        ws = Workspace.load(ws_root)
        path = ws.paths.unattributed_jsonl("widget")
        self.assertEqual(write_unattributed(ws, "widget", self.report), 2)
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertIn('"_meta"', lines[0])
        body = [__import__("json").loads(ln) for ln in lines[1:]]
        self.assertEqual(sorted(r["decl"] for r in body),
                         sorted([SECTION_ONLY_DECL, NAMESPACE_ONLY_DECL]))
        # a later extraction with nothing unattributed must not leave the old
        # list behind: a stale one sends a worker to declarations that have
        # since been attributed or deleted
        self.assertEqual(write_unattributed(ws, "widget", {}), 0)
        self.assertFalse(path.exists())

    def test_a_table_that_agrees_with_its_sources_raises_no_warning(self):
        from cflib.adapters_api import cross_check_warning

        self.assertIsNone(cross_check_warning("widget", {}))
        self.assertIsNone(cross_check_warning("widget", {"lean_scan": {"omissions": []}}))

    def test_the_extract_key_moves_with_the_lean_sources(self):
        """Adding a declaration to a Companions section must re-extract.

        Without the Lean sources in the key, the export's sha is unchanged and
        `cf extract` replays the table that predates the declaration -- which is
        the same short corpus, one commit later.
        """
        before = self.adapter.key()
        path = self.repo / "Widget" / "Basic.lean"
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original.replace(
                "end Companions",
                "/-- Exercise 1.2.22, a later addition. -/\n"
                "theorem gadget_later : True := trivial\n\nend Companions"), encoding="utf-8")
            self.assertNotEqual(self.adapter.key(), before)
        finally:
            path.write_text(original, encoding="utf-8")


class TestProvenanceIsStampedByEveryShippedAdapter(unittest.TestCase):
    """Nullable in the schema, but no adapter in this repo leaves it null.

    `provenance` is what lets a finding say whether "no declaration states this"
    searched a repository or only an export OF one, so a shipped adapter that
    stopped setting it would give that answer back its old ambiguity without
    failing anything.
    """

    def test_the_attribution_table_stamps_both_of_its_artifacts(self):
        repo = _attribution_repo(tmpdir("cf-rf-adapters-prov1-"))
        rows = list(_attribution_adapter(repo).extract(sources={"s": SHA}))
        self.assertEqual({r["provenance"] for r in rows}, {"table", "lean_scan"})

    def test_the_docstring_kind_stamps_docstring(self):
        repo = _backticked_repo(tmpdir("cf-rf-adapters-prov2-"))
        adapter = adapters_api.from_config({
            "adapter": "docstring_tags", "name": "ours", "repo": str(repo),
            "glob": "Src/*.lean", "sources": {"default": "s"}})
        rows = list(adapter.extract(sources={"s": SHA}))
        self.assertTrue(rows)
        self.assertEqual({r["provenance"] for r in rows}, {"docstring"})

    def test_the_blueprint_kind_stamps_blueprint(self):
        repo = _blueprint_repo(tmpdir("cf-rf-adapters-prov3-"))
        adapter = adapters_api.from_config({
            "adapter": "blueprint_tex", "name": "bp", "repo": str(repo),
            "sources": {"default": "s"}})
        rows = list(adapter.extract(sources={"s": SHA}))
        self.assertTrue(rows)
        self.assertEqual({r["provenance"] for r in rows}, {"blueprint"})

    def test_the_vocabulary_has_one_definition(self):
        from adapters import base

        self.assertEqual(base.PROVENANCES, vocab.ROW_PROVENANCE)


class TestTheWorkerIsToldAbsenceIsNotEvidence(unittest.TestCase):
    """The prompt half of the same defect.

    The corpus fix makes the false COVERAGE harder to reach; it cannot make it
    impossible, because a table can be short for reasons no scan sees.  So the
    rule is also stated where the claim gets written -- and only in the runs
    whose gate would accept the kind.
    """

    @staticmethod
    def _render(name: str) -> dict:
        from plan import presets as presets_mod, render_prompts as rp

        preset = presets_mod.get(name)
        ctx = rp.build_context(
            preset=preset, run_id="r1", source_id="s", book_citation="A Book",
            book_text="/srv/runs/sources/s/text.txt", source_sha="a" * 64, page_offset=4,
            companions=[{"name": "ours", "path": "/srv/runs/repos/ours"}],
            gate_command="python3 gate.py")
        return rp.render_prompts(preset, ctx)

    def test_the_unattributed_list_is_named_when_a_companion_has_one(self):
        """Its rows cannot be found by item, so the prompt hands over the file."""
        from plan import presets as presets_mod, render_prompts as rp

        preset = presets_mod.get("differential-text")
        ctx = rp.build_context(
            preset=preset, run_id="r1", source_id="s", book_citation="A Book",
            book_text="/srv/runs/sources/s/text.txt", source_sha="a" * 64, page_offset=4,
            companions=[{"name": "ours", "path": "/srv/runs/repos/ours"},
                        {"name": "mt", "path": "/srv/runs/repos/mt", "unattributed": 17}],
            gate_command="python3 gate.py")
        out = rp.render_prompts(preset, ctx)
        self.assertTrue(ctx["unattributed_any"])
        for name in ("worker-system.md", "reviewer-system.md"):
            with self.subTest(prompt=name):
                self.assertIn("unattributed.jsonl", out[name])
        worker = out["worker-system.md"]
        self.assertIn("`data/mt.unattributed.jsonl` — 17 declaration(s) of `mt`", worker)
        self.assertNotIn("ours.unattributed.jsonl", worker)
        self.assertIn("no search of `<repo>.jsonl` or `pairs.jsonl` by item can ever find them",
                      worker)

    def test_a_run_with_nothing_unattributed_does_not_mention_the_file(self):
        worker = self._render("differential-text")["worker-system.md"]
        self.assertNotIn("unattributed.jsonl", worker)

    def test_a_coverage_run_demands_a_grep_of_the_lean(self):
        worker = self._render("differential-text")["worker-system.md"]
        self.assertIn("Absence from `data/<repo>.jsonl` is **never** sufficient", worker)
        self.assertIn("grep -rn --include=*.lean", worker)
        self.assertIn("/srv/runs/repos/ours", worker)
        self.assertIn("Report `COVERAGE` only when the grep came back empty", worker)

    def test_an_attack_run_is_not_taught_a_kind_its_gate_rejects(self):
        from plan import presets as presets_mod

        self.assertFalse(presets_mod.get("attack-hunt").wants_coverage)
        worker = self._render("attack-hunt")["worker-system.md"]
        self.assertNotIn("Report `COVERAGE` only when", worker)

    def test_the_table_is_described_as_an_index_not_an_inventory(self):
        for name in ("differential-text", "attack-hunt"):
            worker = self._render(name)["worker-system.md"]
            with self.subTest(preset=name):
                self.assertIn("These tables are not the repositories", worker)
                self.assertIn("never as an inventory", worker)

    def test_the_reviewer_must_open_the_lean_for_an_absence_claim(self):
        reviewer = self._render("differential-text")["reviewer-system.md"]
        self.assertIn("**always** when the finding rests on something being ABSENT", reviewer)
        self.assertIn("grep -rn --include=*.lean", reviewer)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
