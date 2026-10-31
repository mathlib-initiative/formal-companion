"""The documents' factual claims, checked against the thing they describe.

`tests/test_rf_docs.py` pins the docs-to-CLI contract: a command, a flag or a
path a document names has to exist.  The final review found the next layer
down — claims that are *well-formed and false*, which that contract cannot see:

* `SKILL.md`'s frontmatter parsed as prose but not as YAML, and Claude Code
  drops every frontmatter field when the block fails to parse, so the shipped
  skill loaded with no name and no description at all;
* the normative task schema in `references/findings-schema-v2.md` §2 named a
  key no template writes, a status no run writes and a title prefix no preset
  can produce;
* `CONTRIBUTING.md` named a pinned regression figure that exists nowhere and
  two the repo had already retired;
* the measured onboarding figures in two reference documents belonged to a
  companion checkout that had moved on, and disagreed with `README.md`'s copy
  of the same measurement;
* `README.md` credited the synthetic end-to-end script with the *other*
  script's check count, in the one paragraph whose job is to be trustworthy
  about numbers;
* `CHANGELOG.md` told a reader to keep five implementations of `finding_id` in
  lockstep; there are two, and the other three delegate.

Every check here derives the truth from the artifact — the parser, the planner,
the pin file, the script, the source — rather than restating it, because a
number retyped into a test drifts exactly the way the prose did.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
# See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import ast
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def read(name: str) -> str:
    return (SKILL / name).read_text(encoding="utf-8")


#: Every tracked document, `docs/design/archive/` excluded: an archived record
#: describes the tree of the day it was written, and its banner says so.
def documents() -> list[str]:
    names = [p.name for p in sorted(SKILL.glob("*.md"))]
    names += [f"references/{p.name}" for p in sorted((SKILL / "references").glob("*.md"))]
    names += [f"docs/{p.name}" for p in sorted((SKILL / "docs").glob("*.md"))]
    names += [f"docs/design/{p.name}" for p in sorted((SKILL / "docs" / "design").glob("*.md"))]
    names += [f"docs/proposals/{p.name}" for p in sorted((SKILL / "docs" / "proposals").glob("*.md"))]
    return names


# ---------------------------------------------------------------------------
# SKILL.md's frontmatter is the only thing a router reads
# ---------------------------------------------------------------------------

class TestTheFrontmatterIsYamlAParserWillRead(unittest.TestCase):
    """A description ending "not a fixer: it reads …" is not YAML.

    `tests/test_rf_docs.py` splits the block on ``---`` and substring-matches
    it, which passes over a file no parser will read.  Claude Code does not
    fail loudly on a bad block: it loads the skill with empty metadata, so the
    router has neither the name nor the description to trigger on — the
    description rewrite defeats itself.
    """

    def block(self) -> str:
        match = re.match(r"^---\n(.*?)\n---\n", read("SKILL.md"), re.S)
        self.assertIsNotNone(match, "SKILL.md has no YAML frontmatter")
        return match.group(1)

    def test_no_value_is_an_unquoted_scalar_holding_a_colon_space(self):
        """The stdlib-only half: `cflib.yamlio`'s fallback accepts the defect.

        ``load_yaml_minimal`` splits on the first ``": "`` and keeps the rest of
        the line, so a test written against the repo's own loader on a machine
        without PyYAML would pass over exactly this bug.
        """
        for line in self.block().splitlines():
            key, sep, value = line.partition(": ")
            if not sep or line.startswith((" ", "\t", "#")):
                continue
            with self.subTest(key=key):
                if value[:1] in ('"', "'", ">", "|"):
                    continue                       # quoted or folded: a colon is fine
                self.assertNotIn(": ", value,
                                 f"`{key}` is an unquoted YAML scalar containing "
                                 f"': '; quote the value or fold it with '>' -- "
                                 f"Claude Code drops every frontmatter field when "
                                 f"the block fails to parse")

    def test_the_description_stays_under_the_router_limit(self):
        match = re.search(r"(?m)^description: (.*)$", self.block())
        self.assertIsNotNone(match, "the frontmatter names no description")
        self.assertLess(len(match.group(1)), 1024)

    def test_a_real_parser_reads_it(self):
        try:
            import yaml
        except ImportError:                                  # pragma: no cover
            self.skipTest("PyYAML not importable")
        parsed = yaml.safe_load(self.block())
        self.assertEqual(sorted(parsed), ["description", "name"])

    def test_the_repos_own_loader_reads_it(self):
        from cflib.yamlio import load_yaml
        parsed = load_yaml(self.block(), prefer_pyyaml=True)
        self.assertEqual(sorted(parsed), ["description", "name"])


# ---------------------------------------------------------------------------
# the normative task schema against the tasks the planner writes
# ---------------------------------------------------------------------------

SCHEMA_DOC = "references/findings-schema-v2.md"


def _schema_section_two() -> str:
    text = read(SCHEMA_DOC)
    start = text.index("\n## 2. ")
    end = text.index("\n## 3. ", start)
    return text[start:end]


def _first_yaml_block(section: str) -> str:
    match = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert match is not None, f"{SCHEMA_DOC} section 2 carries no yaml example"
    return match.group(1)


def _rendered_tasks() -> dict[str, object]:
    """One task per (preset, single-source?) through the real planner."""
    from plan import make_tasks as mt
    from plan import presets as presets_mod

    out = {}
    for name in presets_mod.RUNNABLE:
        preset = presets_mod.get(name)
        for single in (True, False):
            entry = mt.Entry(item="Lemma 2.3.4", repos=["ours", "theirs"],
                             decls={"ours": ["Widget.lemma"]},
                             source_id="widget-theory-2e", printed_page=71,
                             single_source=single)
            tasks = mt.make_tasks(
                [entry], preset,
                {"run_id": "probe", "source_id": "widget-theory-2e",
                 "source_sha": "ab" * 32, "gate_command": "python3 gate.py",
                 "companion": "ours"})
            out[(name, single)] = tasks[0]
    return out


class TestTheTaskSchemaIsTheTaskThePlannerWrites(unittest.TestCase):
    """§2 is what an agent reads to know the task schema, and nothing ran it.

    It documented `source_id:` (no template emits it), `status: pending` (every
    template writes `open`) and an `ATTACK:` title prefix (no preset can produce
    one — they are `hunt:` and `vac:`).  §2 has no machine-readable counterpart
    the way §3 has `scripts/gate/schema.json`, so three of its eight keys were
    wrong through a wave that added a docs-truth suite.
    """

    @classmethod
    def setUpClass(cls):
        cls.section = _schema_section_two()
        cls.example = _first_yaml_block(cls.section)
        cls.tasks = _rendered_tasks()

    def test_the_documented_key_set_is_the_rendered_key_set(self):
        documented = re.findall(r"(?m)^([a-z_]+):", self.example)
        for (preset, single), task in self.tasks.items():
            rendered = re.findall(r"(?m)^([a-z_]+):", task.text)
            with self.subTest(preset=preset, single_source=single):
                self.assertEqual(documented, rendered,
                                 f"{SCHEMA_DOC} section 2 documents {documented}, "
                                 f"and the planner writes {rendered}")

    def test_the_documented_status_is_the_rendered_status(self):
        documented = re.search(r"(?m)^status: (\S+)", self.example).group(1)
        for (preset, single), task in self.tasks.items():
            with self.subTest(preset=preset, single_source=single):
                self.assertEqual(
                    documented, re.search(r"(?m)^status: (\S+)", task.text).group(1),
                    f"{SCHEMA_DOC} section 2 says status: {documented}")

    def test_the_documented_title_prefixes_are_exactly_the_produced_ones(self):
        comment = re.search(r"(?m)^title: .*?#(.*)$", self.example).group(1)
        documented = {p for p in re.findall(r"([A-Za-z][A-Za-z-]*:)", comment)}
        documented |= set(re.findall(r'(?m)^title: "([A-Za-z][A-Za-z-]*:)', self.example))
        produced = {re.search(r'(?m)^title: "([A-Za-z][A-Za-z-]*:)', t.text).group(1)
                    for t in self.tasks.values()}
        self.assertEqual(documented, produced,
                         f"{SCHEMA_DOC} section 2 lists the title prefixes "
                         f"{sorted(documented)}; the presets produce "
                         f"{sorted(produced)}")

    def test_the_documented_id_is_qualified_the_way_the_planner_qualifies_it(self):
        documented = re.search(r"(?m)^id: (\S+)", self.example).group(1)
        self.assertIn("--", documented,
                      f"{SCHEMA_DOC} section 2 shows a bare item slug; a task id "
                      f"carries its source whenever the entry has one")
        for (preset, single), task in self.tasks.items():
            with self.subTest(preset=preset, single_source=single):
                self.assertTrue(task.id.startswith("widget-theory-2e--"), task.id)


# ---------------------------------------------------------------------------
# the pinned regression figures live in one file
# ---------------------------------------------------------------------------

class TestAPinnedFigureInProseIsAPinnedFigure(unittest.TestCase):
    """`CONTRIBUTING.md` named "teorth 49/40/18" — 49 is nowhere, 40/18 retired.

    The one job of that sentence is to tell a second maintainer which numbers
    must not move, so a figure re-typed into it is the drift the pin file was
    added to end.
    """

    def pinned(self) -> set[int]:
        data = json.loads(read("tests/baseline/regression-figures.json"))
        return {int(v["value"]) for v in data["figures"].values()
                if isinstance(v.get("value"), int)}

    def test_every_slash_tuple_beside_the_pinned_figures_is_a_pinned_figure(self):
        pinned = self.pinned()
        for name in documents():
            for para in re.split(r"\n\s*\n", read(name)):
                if "pinned regression figures" not in para:
                    continue
                for tuple_text in re.findall(r"\b\d+(?:/\d+)+\b", para):
                    for part in tuple_text.split("/"):
                        with self.subTest(doc=name, figure=tuple_text):
                            self.assertIn(
                                int(part), pinned,
                                f"{name} names {tuple_text} as a pinned regression "
                                f"figure; {part} is not a value in "
                                f"tests/baseline/regression-figures.json")


# ---------------------------------------------------------------------------
# the measured figures: one measurement, one set of numbers
# ---------------------------------------------------------------------------

MEASURED_DOCS = ("README.md", "docs/running-an-audit.md", "references/onboarding-contract.md",
                 "references/traps.md")
#: "0.91 (271 ok / 27 mismatched)" and its short form "0.68 (202 / 95)".  Matched
#: against a whitespace-flattened document: prose wraps, and a figure the line
#: break falls inside is exactly the one a re-measurement forgets.
_AGREEMENT = re.compile(r"(0\.\d\d) \((\d+)(?: ok)? / (\d+)(?: mismatched)?\)")


class TestOneMeasurementIsOneSetOfNumbers(unittest.TestCase):
    """One commit fixed README's anchor figures and left two documents behind.

    A commit-pinned measurement is falsifiable in a way a dateless one is not:
    the reader can re-run it.  These documents quote the same two anchor
    agreements, so they can be checked against each other with no companion
    repository and no PDF present.  The onboarding transcript that carries them
    moved from README.md to docs/running-an-audit.md, and the check moved with it.
    """

    def agreements(self) -> list[tuple[str, str, int, int]]:
        found = []
        for name in MEASURED_DOCS:
            flat = " ".join(read(name).split())
            for agreement, ok, mismatched in _AGREEMENT.findall(flat):
                found.append((name, agreement, int(ok), int(mismatched)))
        self.assertTrue(found, "no anchor-agreement figure was found to check")
        return found

    def test_the_same_ok_count_never_carries_two_mismatch_counts(self):
        by_ok: dict[int, tuple[str, int]] = {}
        for name, _agreement, ok, mismatched in self.agreements():
            first = by_ok.setdefault(ok, (name, mismatched))
            with self.subTest(ok=ok, doc=name):
                self.assertEqual(
                    mismatched, first[1],
                    f"{name} says {ok} ok / {mismatched} mismatched and "
                    f"{first[0]} says {ok} / {first[1]}: one measurement was "
                    f"re-run and the other documents were left behind")

    def test_each_agreement_is_the_ratio_of_the_counts_beside_it(self):
        for name, agreement, ok, mismatched in self.agreements():
            with self.subTest(doc=name, agreement=agreement):
                self.assertEqual(float(agreement), round(ok / (ok + mismatched), 2),
                                 f"{name}: {agreement} is not {ok}/{ok + mismatched}")


# ---------------------------------------------------------------------------
# a count in prose belongs to the script the sentence names
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
                 "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


class TestACheckCountBelongsToTheScriptItNames(unittest.TestCase):
    """README credited `synthetic.sh` with `multisource.sh`'s seventy checks.

    It runs 27.  The sentence sits two lines after "Every count in it belongs
    to that commit", which is the paragraph a stranger uses to decide whether
    the numbers above it can be trusted.
    """

    def test_every_check_count_in_prose_matches_its_script(self):
        checked = 0
        for name in ("README.md", "CLAUDE.md", "SKILL.md", "docs/running-an-audit.md"):
            flat = read(name).replace("\n", " ")
            for sentence in re.split(r"(?<=\.)\s+", flat):
                claim = re.search(r"checks (?:about )?(\w+) artifacts", sentence)
                script = re.search(r"tests/e2e/\w+\.sh", sentence)
                if not claim or not script:
                    continue
                word = claim.group(1).lower()
                claimed = _NUMBER_WORDS.get(word)
                if claimed is None:
                    self.assertTrue(word.isdigit(), f"{name}: unreadable count {word!r}")
                    claimed = int(word)
                sites = len(re.findall(r"(?m)^\s*check ",
                                       (SKILL / script.group(0)).read_text(encoding="utf-8")))
                checked += 1
                with self.subTest(doc=name, script=script.group(0)):
                    self.assertLessEqual(
                        abs(claimed - sites), 0.5 * sites,
                        f"{name}: {script.group(0)} has {sites} check call sites "
                        f"and the prose claims about {claimed}")
        self.assertGreaterEqual(checked, 2, "the sentences this checks were reworded")


# ---------------------------------------------------------------------------
# how many copies of finding_id a reader is told to keep in step
# ---------------------------------------------------------------------------

_COPY_CLAIM = re.compile(
    r"\b(one|two|three|four|five|six|\d+)\s+(?:copies|times|other files)\b")


def finding_id_implementations() -> list[str]:
    """The modules that build the digest themselves rather than delegating.

    A shim that ends ``return _hashing.finding_id(...)`` is not a copy of the
    rule: it is the same function under another name, and nothing about it can
    drift out of step.
    """
    out = []
    for path in sorted(SKILL.glob("scripts/**/*.py")):
        source = path.read_text(encoding="utf-8")
        if "def finding_id(" not in source:
            continue
        for node in ast.parse(source).body:
            if not isinstance(node, ast.FunctionDef) or node.name != "finding_id":
                continue
            delegates = any(getattr(call.func, "attr", None) == "finding_id"
                            for call in ast.walk(node) if isinstance(call, ast.Call))
            if not delegates:
                out.append(str(path.relative_to(SKILL)))
    return out


class TestTheDocsAgreeOnHowManyFindingIdsThereAre(unittest.TestCase):
    """`CHANGELOG.md` kept "the five copies" after the shims began delegating.

    Two wave-4 packages branched from the same base: one made the compat shims
    delegate, the other wrote the CHANGELOG entry, and the merge of the two is
    where the sentence went stale.  The reader of a compatibility contract is
    told to keep five implementations in lockstep and finds two.
    """

    def test_there_are_two_implementations_and_the_documents_say_so(self):
        implementations = finding_id_implementations()
        self.assertEqual(len(implementations), 2, implementations)
        for name in documents():
            # a paragraph, not a sentence: CHANGELOG's claim names the pin test
            # and leaves `finding_id` itself to the sentence before it.
            for para in re.split(r"\n\s*\n", read(name)):
                para = " ".join(para.split())
                if "finding_id" not in para:
                    continue
                claim = _COPY_CLAIM.search(para)
                if not claim:
                    continue
                word = claim.group(1).lower()
                said = int(word) if word.isdigit() else {
                    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}[word]
                with self.subTest(doc=name, said=claim.group(0)):
                    self.assertEqual(
                        said, len(implementations),
                        f"{name} says finding_id has {claim.group(0)}; it is "
                        f"implemented in {len(implementations)} places "
                        f"({', '.join(implementations)}) and the compat shims "
                        f"delegate -- see CLAUDE.md section 4")


# ---------------------------------------------------------------------------
# the two passages the first-run findings asked for
# ---------------------------------------------------------------------------

class TestTheQuickStartNamesTheServerLessPath(unittest.TestCase):
    """Every quick-start run needed an allowlisted host and recorded consent.

    The local fleet is the fleet path that has actually been driven from this
    repo, and the one a reader with no server can follow, but it appeared only
    in `SKILL.md` and in a caveat 160 lines below the quick start.
    """

    def quick_start(self) -> str:
        text = read("README.md")
        start = text.index("## Quick start")
        return text[start:text.index("\n## ", start + 1)]

    def test_the_local_fleet_is_in_the_quick_start(self):
        block = self.quick_start()
        self.assertIn("--local", block,
                      "the quick start has no server-less path: every run in it "
                      "needs an allowlisted host")
        self.assertIn("--journal", block,
                      "the local fleet's transcribe step (`cf harvest <id> "
                      "--journal <workflow dir>`) is what turns it into a corpus")

    def test_the_directory_inventory_names_the_local_runner(self):
        """The inventory moved to docs/layout.md with the rest of the maintainer's
        map, and this pin moved with it; the quick start above stays in the README."""
        inventory = re.search(r"(?m)^workflows/\s+.*$", read("docs/layout.md"))
        self.assertIsNotNone(inventory, "docs/layout.md has no workflows/ inventory row")
        self.assertIn("fleet-local", inventory.group(0))


# ---------------------------------------------------------------------------
# a rate travels with the sample it was measured on
# ---------------------------------------------------------------------------

class TestARateCarriesItsSampleSize(unittest.TestCase):
    """"wrong about 40% of the time" is 4 of 10 rows from one chapter.

    The prompts were fixed to carry the denominator (`templates/PRESETS.md`,
    the rendered `calibration_note`) and `SKILL.md` — the file a model loads
    instead of the code — was not.
    """

    def test_no_rate_in_the_skill_is_stated_without_its_basis(self):
        for para in re.split(r"\n\s*\n", read("SKILL.md")):
            flat = " ".join(para.split())
            if not re.search(r"\b40\s*%", flat):
                continue
            if "references/method.md" in flat:
                continue                     # the index row pointing at the basis
            with self.subTest(paragraph=flat[:70]):
                self.assertIn("adjudicated", flat,
                              "state the sample the rate was measured on "
                              "(4 of 10 adjudicated rows, one chapter of one book)")


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
