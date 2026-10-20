"""A universe spells each repository once, and its library names stay tokens.

New code: regression tests for three findings of the brain4 review against
``scripts/corpus/universe.py`` (references/locate-documents.md sections 6 and 8,
docs/proposals/wikipedia-locate.md sections 2.3 and 11.3):

* F2 -- one repository spelled two ways (``.../lean4`` and ``.../lean4.git``) was
  two repositories to every exact comparison downstream: an absence's one end
  per ``(repo, commit)``, the consumer's ``(repo, root)`` rule, and the
  ``brain_lib`` check, which refused the second spelling as a token naming "two
  repositories".  ``Universe.validate`` and ``add_library`` refuse the second
  spelling on ``corpus.universe.repo_key``; everything else stays exact;
* F3 -- a ``brain_lib`` outside ``BRAIN_LIB`` reached the record set unflagged
  whenever it was not a new row's (a frozen universe, a re-add, the id fallback):
  the export report names every one, a re-add's ``--brain-lib`` must be a token,
  and ``Mathlib`` and ``MATHLIB`` are one token, compared without case;
* F5 -- a library id is a YAML scalar too: it starts with a letter and is none of
  YAML's bool and null words, so none reads as anything but itself even written bare,
  and loading names an id a hand-written file let YAML type (the writer's half is
  ``tests/test_yamlio_lookalikes.py``).

Synthetic libraries and ``example.org`` / ``github.com`` spellings only.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import random
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for _path in (SCRIPTS, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import _locate_fixtures as F  # noqa: E402
import test_locate_recordset_format as recordset  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402
from cflib import yamlio  # noqa: E402
from cflib.yamlio import load_yaml, load_yaml_minimal, read_yaml  # noqa: E402
from corpus.universe import (LIBRARY_ID, YAML_WORD_IDS, Universe, repo_key,  # noqa: E402
                             written_brain_lib)
from locate import export as export_mod, inputs, records  # noqa: E402

TOOLCHAIN = "leanprover/lean4:v4.34.0"
LEAN4 = "https://github.com/leanprover/lean4"
CORE = "c" * 40
DOCS = HERE.parent / "references" / "locate-documents.md"


def _doc_section(number: int) -> str:
    """Section ``number`` of references/locate-documents.md, heading to next heading."""
    text = DOCS.read_text(encoding="utf-8")
    start = re.search(rf"^## {number}\. ", text, re.M)
    end = re.search(rf"^## {number + 1}\. ", text, re.M)
    assert start and end, number
    return text[start.start():end.start()]


def _scratch(case) -> Path:
    path = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def _universe(case, universe_id: str = "u1") -> Universe:
    path = _scratch(case) / "corpora" / universe_id / "universe.yaml"
    path.parent.mkdir(parents=True)
    return Universe.create(path, universe_id, "2026-10-17")


def _pin(library_id: str, repo: str, **kw) -> dict:
    return dict(library_id=library_id, repo=repo, commit=kw.pop("commit", CORE),
                toolchain=TOOLCHAIN, roots=kw.pop("roots", ["Init"]), **kw)


def _row(library_id: str, repo: str, brain_lib: str | None) -> dict:
    """A row as a universe file holds it, for one written by hand or before a rule."""
    return {"id": library_id, "brain_lib": brain_lib, "repo": repo, "commit": CORE,
            "toolchain": TOOLCHAIN, "roots": ["Init"], "licence": None, "conventions": None,
            "index": None}


class TestTheRepositoryKey(unittest.TestCase):
    """F2: ``repo_key`` is one key per repository, whichever way it is spelled."""

    def test_the_spellings_of_one_repository_are_one_key(self):
        for spelling in (LEAN4, LEAN4 + ".git", "https://www.github.com/leanprover/lean4",
                         "https://GitHub.com/LeanProver/Lean4.git",
                         "https://WWW.GITHUB.COM/leanprover/LEAN4"):
            with self.subTest(repo=spelling):
                self.assertEqual(repo_key(spelling), repo_key(LEAN4))
        self.assertEqual(repo_key(LEAN4), LEAN4)

    def test_only_github_folds_the_owner_and_name(self):
        self.assertEqual(repo_key("https://EXAMPLE.org/Group/Beta.git"),
                         "https://example.org/Group/Beta")
        self.assertEqual(repo_key("https://www.example.org/Group/Beta"),
                         "https://example.org/Group/Beta")
        self.assertNotEqual(repo_key("https://example.org/Group/Beta"),
                            repo_key("https://example.org/group/beta"))

    def test_a_trailing_git_is_dropped_in_any_case(self):
        """``.GIT`` is the suffix ``.git`` spelled in capitals, on any host; only the case
        of the owner and name stays significant off github.com."""
        beta = "https://example.org/group/beta"
        for suffix in (".git", ".GIT", ".Git", ".gIT"):
            with self.subTest(suffix=suffix):
                self.assertEqual(repo_key(beta + suffix), beta)
                self.assertEqual(repo_key("https://github.com/o/r" + suffix),
                                 "https://github.com/o/r")
        self.assertNotEqual(repo_key("https://example.org/group/Beta.GIT"), beta)
        self.assertNotEqual(repo_key(beta + "git"), beta)      # no dot, no suffix
        self.assertNotEqual(repo_key(beta + ".gitx"), beta)

    def test_other_repositories_stay_apart(self):
        for other in ("https://github.com/leanprover/lean4-nightly",
                      "https://github.com/leanprover-community/lean4",
                      "https://gitlab.example.org/leanprover/lean4",
                      "https://github.com/leanprover/lean4/sub"):
            with self.subTest(repo=other):
                self.assertNotEqual(repo_key(other), repo_key(LEAN4))


class TestOneSpellingPerRepository(unittest.TestCase):
    """F2: ``lean4`` and ``lean4.git`` in one universe are refused at add and on load."""

    def test_a_second_spelling_is_refused_at_add_naming_the_first(self):
        universe = _universe(self)
        universe.add_library(**_pin("lean-core-init", LEAN4, brain_lib="Init"))
        with self.assertRaises(CfError) as caught:
            universe.add_library(**_pin("lean-core-std", LEAN4 + ".git", brain_lib="Std",
                                        roots=["Std"]))
        self.assertIn(f"repo '{LEAN4}.git' is the repository library 'lean-core-init' spells "
                      f"'{LEAN4}'", str(caught.exception))
        self.assertIn(f"--repo {LEAN4}", caught.exception.hint)
        self.assertEqual(universe.library_ids(), ["lean-core-init"])
        # spelled the universe's way, two libraries share the repository
        universe.add_library(**_pin("lean-core-std", LEAN4, brain_lib="Std", roots=["Std"]))
        self.assertEqual(universe.library_ids(), ["lean-core-init", "lean-core-std"])
        universe.save()

    def test_every_other_spelling_is_refused_too(self):
        universe = _universe(self)
        universe.add_library(**_pin("lean-core-init", LEAN4, brain_lib="Init"))
        for i, spelling in enumerate(("https://www.github.com/leanprover/lean4",
                                      "https://github.com/LeanProver/Lean4",
                                      "https://GITHUB.com/leanprover/lean4.git")):
            with self.subTest(repo=spelling), self.assertRaises(CfError) as caught:
                universe.add_library(**_pin(f"core{i}", spelling, brain_lib="Std"))
            self.assertIn("is the repository library 'lean-core-init' spells",
                          str(caught.exception))

    def test_a_capital_git_suffix_is_a_second_spelling_off_github(self):
        universe = _universe(self)
        beta = "https://example.org/group/beta"
        universe.add_library(**_pin("beta", beta, brain_lib="Beta", roots=["Beta"]))
        for spelling in (beta + ".GIT", beta + ".Git"):
            with self.subTest(repo=spelling), self.assertRaises(CfError) as caught:
                universe.add_library(**_pin("beta-extra", spelling, brain_lib="BetaExtra",
                                            roots=["BetaExtra"]))
            self.assertIn(f"is the repository library 'beta' spells '{beta}'",
                          str(caught.exception))
        universe.data["libraries"].append(_row("beta-extra", beta + ".GIT", "BetaExtra"))
        self.assertEqual(len(universe.validate()), 1, universe.validate())

    def test_the_false_brain_lib_refusal_is_gone(self):
        """One token in one repository spelled two ways is the spelling's refusal, never
        "names two repositories": the repository is one."""
        universe = _universe(self)
        universe.add_library(**_pin("lean-core-init", LEAN4, brain_lib="Lean"))
        with self.assertRaises(CfError) as caught:
            universe.add_library(**_pin("lean-core-std", LEAN4 + ".git", brain_lib="Lean",
                                        roots=["Std"]))
        self.assertIn("is the repository library 'lean-core-init' spells", str(caught.exception))
        self.assertNotIn("already names", str(caught.exception))
        # one repository may carry one token for several libraries
        universe.add_library(**_pin("lean-core-std", LEAN4, brain_lib="Lean", roots=["Std"]))
        self.assertEqual(universe.validate(), [])

        universe.data["libraries"][1]["repo"] = LEAN4 + ".git"
        problems = universe.validate()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(f"library 'lean-core-std': repo '{LEAN4}.git' is the repository library "
                      f"'lean-core-init' spells '{LEAN4}'", problems[0])

    def test_a_universe_file_with_two_spellings_does_not_load(self):
        universe = _universe(self)
        universe.add_library(**_pin("lean-core-init", LEAN4, brain_lib="Init"))
        universe.add_library(**_pin("lean-core-std", LEAN4, brain_lib="Std", roots=["Std"]))
        universe.save()
        text = universe.path.read_text(encoding="utf-8")
        self.assertEqual(text.count(f'repo: "{LEAN4}"\n'), 2)
        universe.path.write_text(text.replace(f'repo: "{LEAN4}"\n', f'repo: "{LEAN4}.git"\n', 1),
                                 encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            Universe.load(universe.path)
        self.assertIn("a universe spells one repository one way", str(caught.exception))

    def test_a_re_add_in_the_other_spelling_says_so(self):
        universe = _universe(self)
        universe.add_library(**_pin("lean-core-init", LEAN4, brain_lib="Init"))
        with self.assertRaises(CfError) as caught:
            universe.add_library(**_pin("lean-core-init", LEAN4 + ".git"))
        self.assertIn(f"spells its repository '{LEAN4}', not '{LEAN4}.git'",
                      str(caught.exception))
        self.assertIn(f"--repo {LEAN4}", caught.exception.hint)
        # another commit is still a new pin, not a spelling
        with self.assertRaises(CfError) as caught:
            universe.add_library(**_pin("lean-core-init", LEAN4 + ".git", commit="d" * 40))
        self.assertIn("is already pinned at", str(caught.exception))

    def test_downstream_the_repo_is_compared_as_written(self):
        """The rule is what keeps ``records.absence_ends``' exact dedupe right: the
        universe that reaches it spells lean4 once, so a split is one end."""
        universe = _universe(self)
        universe.add_library(**_pin("lean-core-init", LEAN4, brain_lib="Init"))
        universe.add_library(**_pin("lean-core-std", LEAN4, brain_lib="Std", roots=["Std"]))
        doc = {"libraries": universe.libraries}
        self.assertEqual(records.absence_ends(doc),
                         [{"type": "lean", "uri": LEAN4, "version": CORE}])
        # and the dedupe itself is exact: it never canonicalises a spelling
        doc = {"libraries": [{**universe.libraries[0]},
                             {**universe.libraries[1], "repo": LEAN4 + ".git"}]}
        self.assertEqual([end["uri"] for end in records.absence_ends(doc)],
                         [LEAN4, LEAN4 + ".git"])


class TestBrainLibTokens(unittest.TestCase):
    """F3: a token names one repository without case, and a re-add's must be a token."""

    def test_tokens_are_compared_without_case_across_repositories(self):
        universe = _universe(self)
        universe.add_library(**_pin("mathlib", "https://example.org/mathlib4",
                                    brain_lib="Mathlib", roots=["Mathlib"]))
        for token in ("MATHLIB", "mathlib", "MathLib"):
            with self.subTest(brain_lib=token), self.assertRaises(CfError) as caught:
                universe.add_library(**_pin("other", "https://example.org/other",
                                            brain_lib=token, roots=["Other"]))
            self.assertIn(f"brain_lib {token!r} already names mathlib as 'Mathlib'",
                          str(caught.exception))
            self.assertIn("compared without case", caught.exception.hint)
            self.assertIn("references/locate-documents.md section 8", caught.exception.hint)
            self.assertNotIn("11.3", caught.exception.hint)
        with self.assertRaises(CfError) as caught:
            universe.add_library(**_pin("other", "https://example.org/other",
                                        brain_lib="Mathlib", roots=["Other"]))
        self.assertIn("brain_lib 'Mathlib' already names mathlib (https://example.org/mathlib4)",
                      str(caught.exception))
        self.assertEqual(universe.library_ids(), ["mathlib"])

    def test_the_section_the_refusal_cites_states_the_case_rule(self):
        """The case rule is the documents' (section 8), not the proposal's: its sections
        2.3 and 11.3 say a token names one repository and say nothing of case."""
        section = " ".join(_doc_section(8).split())
        self.assertIn("**compared without case** (`Mathlib` and `MATHLIB` are one token)",
                      section)

    def test_the_id_fallback_is_a_token_like_any_other(self):
        universe = _universe(self)
        universe.add_library(**_pin("alpha", "https://example.org/alpha", roots=["Alpha"]))
        self.assertEqual(universe.library("alpha")["brain_lib"], "alpha")
        with self.assertRaises(CfError) as caught:
            universe.add_library(**_pin("beta", "https://example.org/beta", brain_lib="ALPHA",
                                        roots=["Beta"]))
        self.assertIn("already names alpha as 'alpha'", str(caught.exception))
        universe.add_library(**_pin("beta", "https://example.org/beta", brain_lib="Beta",
                                    roots=["Beta"]))
        with self.assertRaises(CfError):                 # the fallback against a given token
            universe.add_library(**_pin("beta2", "https://example.org/beta2", roots=["B"],
                                        brain_lib="BETA"))

    def test_a_universe_file_with_two_cases_of_one_token_does_not_load(self):
        universe = _universe(self)
        universe.data["libraries"] = [_row("mathlib", "https://example.org/mathlib4", "Mathlib"),
                                      _row("other", "https://example.org/other", "MATHLIB")]
        self.assertEqual(universe.validate(),
                         ["brain_lib 'MATHLIB' and 'Mathlib' are one token (compared without "
                          "case) and name two repositories"])
        universe.data["libraries"][1]["brain_lib"] = "Mathlib"
        self.assertEqual(universe.validate(), ["brain_lib 'Mathlib' names two repositories"])
        universe.data["libraries"][1]["brain_lib"] = "Other"
        self.assertEqual(universe.validate(), [])
        # a row with no brain_lib is written under its id, and checked under it
        universe.data["libraries"][1]["brain_lib"] = None
        universe.data["libraries"][1]["id"] = "mathlib-other"
        universe.data["libraries"].append(_row("mathLIB", "https://example.org/x", None))
        self.assertIn("are one token", " ".join(universe.validate()))

    def test_a_re_add_whose_brain_lib_is_no_token_is_refused(self):
        universe = _universe(self)
        universe.data["libraries"].append(_row("ff-adic", "https://example.org/adic",
                                               "FF.AdicModules"))
        universe.save()
        again = Universe.load(universe.path)
        pin = _pin("ff-adic", "https://example.org/adic")
        self.assertEqual(again.add_library(**pin)["brain_lib"], "FF.AdicModules")
        # the very name the row records: no token, so refused
        with self.assertRaises(CfError) as caught:
            again.add_library(**pin, brain_lib="FF.AdicModules")
        self.assertIn("--brain-lib 'FF.AdicModules' is not a library token", str(caught.exception))
        self.assertIn("--brain-lib FF_AdicModules", caught.exception.hint)
        # another name, token or not, is the recorded-token refusal, as before
        for token in ("FF_AdicModules", "FF AdicModules"):
            with self.subTest(brain_lib=token), self.assertRaises(CfError) as caught:
                again.add_library(**pin, brain_lib=token)
            self.assertIn("already records brain_lib 'FF.AdicModules'", str(caught.exception))
        self.assertEqual(again.library("ff-adic")["brain_lib"], "FF.AdicModules")

    def test_written_brain_lib_is_the_rows_own_else_its_id(self):
        self.assertEqual(written_brain_lib({"id": "lean-core", "brain_lib": None}), "lean-core")
        self.assertEqual(written_brain_lib({"id": "lean-core", "brain_lib": ""}), "lean-core")
        self.assertEqual(written_brain_lib({"id": "lean-core", "brain_lib": "Init"}), "Init")


class TestTheExportReportFlagsNonTokens(recordset._Exported):
    """F3: the export report names every written ``brain_lib`` that is no token, the id
    fallback included, and exports anyway (u1's ``FF.<Root>`` still exports)."""

    #: inherited from the planner's Campaign; it runs in that module already
    test_the_curated_joins_and_the_lexical_hits = None

    def test_the_report_names_each_non_token_and_the_set_still_exports(self):
        self.challenge()
        path = self.paths.universe_yaml(F.U)
        universe = Universe.load(path)
        universe.library("beta")["brain_lib"] = "FF.Beta"
        universe.library("alpha")["brain_lib"] = None          # written under its id, a token
        universe.save()
        code, out, err = self.export()
        self.assertEqual(code, 0, out + err)
        report = read_json(self.paths.export_report_json("set-1"))
        self.assertEqual(report["brain_lib_not_token"],
                         [{"library": "beta", "brain_lib": "FF.Beta", "from_id": False}])
        self.assertIn("1 library(ies) carry a brain_lib that is no library token", out + err)
        self.assertIn("beta (FF.Beta)", out + err)
        uni = read_json(self.paths.export_dir("set-1") / "universe.json")
        self.assertEqual({lib["id"]: lib["brain_lib"] for lib in uni["libraries"]},
                         {"alpha": "alpha", "beta": "FF.Beta"})

    def test_a_universe_of_tokens_flags_nothing(self):
        self.challenge()
        self.assertEqual(self.export()[0], 0)
        report = read_json(self.paths.export_report_json("set-1"))
        self.assertEqual(report["brain_lib_not_token"], [])

    def test_the_id_fallback_is_flagged_by_name(self):
        rows = {"lean-core": _row("lean-core", LEAN4, None),
                "ff-adic": _row("ff-adic", "https://example.org/adic", "FF.AdicModules"),
                "mathlib": _row("mathlib", "https://example.org/mathlib4", "Mathlib"),
                "beta": _row("beta", "https://example.org/beta", None)}
        universe = inputs.Universe(id="u1", sha256="0" * 64, libraries=rows, excluded=[],
                                   owner=None)
        self.assertEqual(export_mod.brain_lib_not_token(universe), [
            {"library": "ff-adic", "brain_lib": "FF.AdicModules", "from_id": False},
            {"library": "lean-core", "brain_lib": "lean-core", "from_id": True}])


class TestALibraryIdStartsWithALetter(unittest.TestCase):
    """F5: an id is written into YAML; one starting with a digit could be a number or a
    date look-alike, so a library id and a universe id start with a letter."""

    def test_an_id_that_starts_with_a_digit_is_refused(self):
        universe = _universe(self)
        for bad in ("1.5e-3", "0x10", "2026-10-17", "1lib", "4ti2", "0", "_lib", "-lib", ".lib"):
            with self.subTest(library_id=bad), self.assertRaises(CfError) as caught:
                universe.add_library(**_pin(bad, f"https://example.org/x{len(bad)}",
                                            brain_lib="X"))
            self.assertIn("a lowercase letter first", str(caught.exception))
            with self.subTest(universe_id=bad), self.assertRaises(CfError):
                Universe.create(_scratch(self) / "universe.yaml", bad)
        for good in ("u1", "mathlib", "lean-core", "ff-adic-modules", "a", "x1.5e-3", "lib_2"):
            with self.subTest(library_id=good):
                self.assertTrue(LIBRARY_ID.match(good))
                universe.add_library(**_pin(good, f"https://example.org/{good}",
                                            brain_lib=f"L{len(universe.libraries)}"))
        universe.save()
        self.assertEqual(Universe.load(universe.path).library_ids(), sorted(
            ("u1", "mathlib", "lean-core", "ff-adic-modules", "a", "x1.5e-3", "lib_2")))

    def test_a_universe_file_with_such_an_id_does_not_load(self):
        universe = _universe(self)
        universe.data["libraries"].append(_row("1e3", "https://example.org/x", "X"))
        problems = universe.validate()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("id '1e3' is not [a-z][a-z0-9._-]*", problems[0])

    def test_a_yaml_bool_or_null_word_is_no_id(self):
        """``no`` reads as False and ``null`` as None wherever an id is written bare, so
        neither is an id, at add, at an exclusion or as a universe's id."""
        universe = _universe(self)
        self.assertEqual(YAML_WORD_IDS, ("yes", "no", "true", "false", "on", "off", "null"))
        for word in YAML_WORD_IDS:
            with self.subTest(library_id=word), self.assertRaises(CfError) as caught:
                universe.add_library(**_pin(word, f"https://example.org/{word}",
                                            brain_lib="X"))
            self.assertIn("no YAML bool or null word", str(caught.exception))
            with self.subTest(excluded=word), self.assertRaises(CfError):
                universe.exclude(word, "synthetic")
            with self.subTest(universe_id=word), self.assertRaises(CfError):
                Universe.create(_scratch(self) / "universe.yaml", word)
        # an id that only contains or starts with one is an id
        for good in ("nob", "only", "nullable", "offline", "yes2", "true-lib", "on.x", "n",
                     "y", "nan", "inf", "noo"):
            with self.subTest(library_id=good):
                self.assertTrue(LIBRARY_ID.match(good))

    def test_the_words_are_every_word_the_writer_quotes_that_could_be_an_id(self):
        """Lockstep with ``cflib.yamlio``: each bool or null word its writer quotes, spelled
        as an id could be, is one ``LIBRARY_ID`` refuses."""
        shaped = re.compile(r"[a-z][a-z0-9._-]*\Z")
        quoted = {w for w in yamlio._TYPED_WORDS if shaped.match(w)}
        self.assertEqual(quoted, set(YAML_WORD_IDS))

    def test_no_id_reads_as_anything_but_itself_written_bare(self):
        """Every string ``LIBRARY_ID`` admits, written unquoted, is that string to both
        readers: PyYAML's resolver words, then a seeded battery of number-, date- and
        word-shaped ids."""
        rng = random.Random(20261019)
        candidates = set(YAML_WORD_IDS) | {"nan", "inf", "e1", "x10", "b1", "o7", "n", "y",
                                           "a1e3", "t2026-10-17", "on_", "nulls", "off-by-one"}
        alphabet = "abenoxyfltru0123456789._-"
        for _ in range(4000):
            candidates.add(rng.choice("abenoxyflt") + "".join(
                rng.choice(alphabet) for _ in range(rng.randint(0, 9))))
        readers = [load_yaml_minimal] + ([load_yaml] if yamlio.HAVE_PYYAML else [])
        admitted = sorted(c for c in candidates if LIBRARY_ID.match(c))
        self.assertGreater(len(admitted), 3000)
        for reader in readers:
            for lid in admitted:
                value = reader(f"id: {lid}\n")["id"]
                if value != lid:
                    self.fail(f"{reader.__name__} read the id {lid!r} as {value!r}")

    def test_a_hand_written_typed_id_is_named_on_load(self):
        """A universe file someone wrote by hand with a bare ``no``: both readers type it,
        and loading refuses it -- the universe's id, a library's or an exclusion's --
        naming the type it was read as."""
        lib = _row("alpha", "https://example.org/alpha", "Alpha")
        text = (f"id: no\nfrozen: '2026-10-17'\nlibraries:\n- id: off\n"
                f"  brain_lib: Alpha\n  repo: {lib['repo']}\n  commit: {CORE}\n"
                f"  toolchain: {TOOLCHAIN}\n  roots: [Alpha]\n  licence: {{spdx: MIT}}\n"
                f"excluded:\n- id: on\n  reason: did not build\n")
        readers = [load_yaml_minimal] + ([load_yaml] if yamlio.HAVE_PYYAML else [])
        for reader in readers:
            with self.subTest(reader=reader.__name__):
                data = reader(text)
                problems = Universe(_scratch(self) / "universe.yaml", data).validate()
                self.assertIn("id False was read as a YAML bool, not a string", problems[0])
                self.assertTrue(any(p.startswith("library ") and
                                    ": id False was read as a YAML bool" in p
                                    for p in problems), problems)
                self.assertTrue(any("an exclusion's id True was read as a YAML bool" in p
                                    for p in problems), problems)
        path = _scratch(self) / "universe.yaml"
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(CfError) as caught:
            Universe.load(path)
        self.assertIn("was read as a YAML bool, not a string", str(caught.exception))
        # quoted, the words are strings, and still no ids
        quoted = Universe(path, load_yaml_minimal(text.replace("id: on", "id: 'on'")))
        self.assertTrue(any("an exclusion's id 'on' is not" in p for p in quoted.validate()))

    def test_the_shipped_fixtures_ids_all_start_with_a_letter(self):
        for lid in F.LIBRARIES:
            with self.subTest(library_id=lid):
                self.assertTrue(LIBRARY_ID.match(lid))
        self.assertTrue(LIBRARY_ID.match(F.U))

    def test_a_universe_round_trips_through_both_readers(self):
        """The ids, a date, an all-digit commit and an exponent-shaped one, as both readers
        read the file cf wrote."""
        universe = _universe(self)
        commits = {"alpha": "1" * 40, "beta": "1e" + "0" * 38}
        for lid, commit in commits.items():
            universe.add_library(**_pin(lid, f"https://example.org/{lid}", commit=commit,
                                        roots=[lid.capitalize()], licence="0BSD"))
        universe.save()
        text = universe.path.read_text(encoding="utf-8")
        for reader in (load_yaml, load_yaml_minimal):
            with self.subTest(reader=reader.__name__):
                data = reader(text)
                self.assertEqual(data["frozen"], "2026-10-17")
                self.assertEqual({lib["id"]: lib["commit"] for lib in data["libraries"]},
                                 commits)
                self.assertEqual({lib["licence"]["spdx"] for lib in data["libraries"]},
                                 {"0BSD"})
        self.assertEqual(read_yaml(universe.path), read_yaml(universe.path, prefer_pyyaml=False))


if __name__ == "__main__":
    unittest.main()
