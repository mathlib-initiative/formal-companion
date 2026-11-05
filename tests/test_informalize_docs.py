"""The documents a reader starts from, held to the informalize mode the CLI has.

New code, for docs/proposals/informalize.md section 11, component 8: `SKILL.md`'s
command index and its Informalization section, `README.md`, `docs/status.md` and the
trigger eval set. Those four are what a person and a router read before any of
`scripts/informalize/` is loaded, and nothing else notices when they describe a mode
this checkout does not have. Four kinds of check:

* every `cf informalize` subcommand the CLI registers is in SKILL.md's command index,
  every one the index names is registered, none of the registered ones sits in the
  row's "not implemented" tail, and the range of components the row and
  `docs/status.md` call built is the same range: the row said "components 2 to 4"
  while three more had landed, and nothing noticed;
* what `docs/status.md` calls built is a command, and what it calls not implemented is
  not one -- so the page cannot go on promising `cf informalize apply` after it lands,
  or keep calling it missing;
* the description a router reads advertises the mode, and the eval set measures it:
  a positive prompt for a mode nothing in the description names measures nothing;
* the documents stay generic. The first target's library, theorem and reference work
  belong to the specification's acceptance section and to the host runbook; this file
  reads those names from that section rather than spelling them, as
  `tests/test_informalize_spec.py` does.
"""
from __future__ import annotations

# Pin HOME before anything imports the CLI: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
# See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import argparse
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

import cf as cf_main                                          # noqa: E402

SPEC = (SKILL / "docs" / "proposals" / "informalize.md").read_text(encoding="utf-8")


def read(name: str) -> str:
    return (SKILL / name).read_text(encoding="utf-8")


def informalize_subcommands() -> set[str]:
    """What `cf informalize <sub>` this checkout really has, from the live parsers."""
    parser = argparse.ArgumentParser(prog="cf")
    subparsers = parser.add_subparsers(dest="command")
    cf_main.discover_commands(subparsers)
    mode = subparsers.choices.get("informalize")
    if mode is None:                                          # pragma: no cover
        return set()
    for action in mode._actions:                              # noqa: SLF001 - no public API
        if isinstance(action, argparse._SubParsersAction):    # noqa: SLF001
            return set(action.choices)
    return set()                                              # pragma: no cover


#: Every subcommand the specification's sections 3 and 11 name, built or not.
SPECIFIED = {"init", "sync", "status", "build-command", "fetch-library", "scope",
             "check-items", "approve", "reference-checklist", "apply", "pdf-command",
             "fetch-pdf", "overlay", "check-site", "verso-command"}


class TestTheCommandIndexIsThisCheckout(unittest.TestCase):
    """SKILL.md is loaded whole into a session: a name in it is a promise."""

    def setUp(self):
        self.built = informalize_subcommands()
        self.skill = read("SKILL.md")
        if not self.built:                                    # pragma: no cover
            self.skipTest("cf informalize is not built in this checkout")

    def index_row(self) -> str:
        row = [line for line in self.skill.splitlines()
               if line.startswith("| `informalize ")]
        self.assertEqual(len(row), 1, "SKILL.md has no single `cf informalize` index row")
        return row[0]

    def test_every_built_subcommand_is_in_the_index(self):
        row = self.index_row()
        for name in sorted(self.built):
            with self.subTest(subcommand=name):
                self.assertIn(name, row, f"the index row omits `cf informalize {name}`")

    def test_the_index_row_calls_no_built_subcommand_missing(self):
        """The row's "not implemented" tail is what goes stale as components land."""
        row = self.index_row()
        if "not implemented" not in row:                      # pragma: no cover
            self.skipTest("the index row claims nothing is missing")
        clause = row[:row.index("not implemented")]
        if "—" in clause:
            clause = clause[clause.rindex("—") + 1:]
        for name in sorted(self.built):
            with self.subTest(subcommand=name):
                # either spelling: the row writes `apply` as `cf informalize apply`
                self.assertIsNone(
                    re.search(rf"`(?:cf informalize )?{re.escape(name)}`", clause),
                    f"the index row calls `cf informalize {name}` not "
                    f"implemented, and this checkout has it")

    def test_the_component_range_agrees_with_the_status_page(self):
        """The range is what went stale last time, and two documents carry it."""
        pattern = r"[Cc]omponents (\S+) to (\S+) of"
        section = read("docs/status.md").split("\n## Informalization\n", 1)[1]
        row = re.search(pattern, self.index_row())
        page = re.search(pattern, section.split("\n## ", 1)[0])
        self.assertIsNotNone(row, "SKILL.md's index row gives no component range")
        self.assertIsNotNone(page, "docs/status.md gives no component range")
        self.assertEqual(row.groups(), page.groups(),
                         "SKILL.md and docs/status.md disagree about which "
                         "components of the specification are built")

    def test_a_subcommand_the_index_names_as_built_exists(self):
        """An unbuilt one may be named, but only where the text says it is absent."""
        named = set(re.findall(r"`cf informalize ([a-z][a-z-]*)", self.skill))
        named |= {n for n in SPECIFIED
                  if re.search(rf"\binformalize [a-z |`-]*\b{re.escape(n)}\b", self.skill)}
        for name in sorted(named - self.built):
            with self.subTest(subcommand=name):
                self.assertIn(name, SPECIFIED, f"SKILL.md invents `cf informalize {name}`")
                block = " ".join(b for b in re.split(r"\n\s*\n", self.skill) if name in b)
                self.assertIn("not implemented", block.lower(),
                              f"SKILL.md names `cf informalize {name}`, which this "
                              f"checkout has not got, and does not say so")

    def test_the_section_is_there_and_points_at_the_specification(self):
        self.assertIn("\n## Informalization\n", self.skill)
        section = self.skill.split("\n## Informalization\n", 1)[1].split("\n## ", 1)[0]
        for needle in ("docs/proposals/informalize.md", "cf plan-informalize",
                       "docs/status.md"):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)
        self.assertIn("never builds a library", " ".join(section.split()),
                      "the section does not say that cf builds no Lean")


class TestTheStatusPageAgesWithTheCode(unittest.TestCase):
    """`docs/status.md` is where every other document sends a reader for what has run."""

    def setUp(self):
        self.page = read("docs/status.md")
        self.assertIn("\n## Informalization\n", self.page)
        self.section = self.page.split("\n## Informalization\n", 1)[1].split("\n## ", 1)[0]
        self.built = informalize_subcommands()

    @staticmethod
    def _named(paragraph: str) -> set[str]:
        return {n for n in re.findall(r"`?([a-z][a-z-]*)`?", paragraph)} & SPECIFIED

    def paragraph(self, label: str) -> str:
        found = [p.strip() for p in re.split(r"\n\s*\n", self.section)
                 if p.strip().startswith(label)]
        self.assertEqual(len(found), 1, f"the section has no single {label} paragraph")
        return found[0]

    def test_what_it_calls_built_is_built(self):
        if not self.built:                                    # pragma: no cover
            self.skipTest("cf informalize is not built in this checkout")
        missing = sorted(self._named(self.paragraph("**Built.**")) - self.built)
        self.assertEqual(missing, [], f"docs/status.md calls {missing} built")

    def test_what_it_calls_missing_is_missing(self):
        extra = sorted(self._named(self.paragraph("**Not implemented.**")) & self.built)
        self.assertEqual(extra, [], f"docs/status.md still calls {extra} not implemented")

    def test_it_says_nothing_has_run(self):
        """The one claim a reader acts on: no real library, no host, no PDF."""
        run = " ".join(self.paragraph("**Not run.**").split()).lower()
        for needle in ("no lean library has been built", "no phase has run",
                       "canonical pdf"):
            with self.subTest(needle=needle):
                self.assertIn(needle, run)

    def test_the_end_to_end_list_names_it(self):
        never = self.page.split("## Never exercised end to end", 1)[1].split("\n## ", 1)[0]
        self.assertIn("informalize", never.lower())


class TestTheRouterAndTheEvalSetAgree(unittest.TestCase):
    """A positive prompt for a mode the description never names measures nothing."""

    def setUp(self):
        self.description = re.search(r"(?m)^description: (.*)$", read("SKILL.md")).group(1)
        self.cases = json.loads(read("evals/trigger.json"))

    def test_the_description_advertises_the_mode(self):
        self.assertIn("informaliz", self.description.lower())

    def test_the_description_stays_within_the_router_limit(self):
        """The clause was bought with words cut elsewhere; it may not be bought twice."""
        self.assertLess(len(self.description), 1024)

    def test_one_positive_case_measures_it(self):
        positives = [c for c in self.cases
                     if c["should_trigger"] and "informaliz" in c["why"].lower()]
        self.assertTrue(positives, "no trigger eval measures the informalize mode")
        for case in positives:
            with self.subTest(case=case["id"]):
                query = case["query"].lower()
                self.assertIn("lean", query)
                self.assertNotIn("informaliz", query,
                                 "the prompt quotes the skill's own vocabulary back at "
                                 "it, which measures nothing (evals/README.md)")

    def test_the_readme_names_it_with_its_status(self):
        readme = read("README.md")
        row = [line for line in readme.splitlines() if line.startswith("| [Informalize]")]
        self.assertEqual(len(row), 1, "README.md's tool table has no Informalize row")
        self.assertIn("docs/proposals/informalize.md", row[0])
        self.assertIn("synthetic", row[0].lower(),
                      "the row claims more than docs/status.md records")


class TestTheDocumentsStayGeneric(unittest.TestCase):
    """The first target is an example in the specification and the runbook, nowhere else.

    The names are read from the acceptance section's `cf informalize init` line, so this
    file spells none of them.
    """

    #: the documents component 8 writes, which are about the mode and not about one run
    DOCUMENTS = ("SKILL.md", "README.md", "docs/status.md", "evals/trigger.json",
                 "evals/README.md", "CHANGELOG.md")

    @staticmethod
    def needles() -> list[str]:
        start = SPEC.index("\n## 12. ")
        end = SPEC.index("\n## 13. ")
        (line,) = [ln for ln in SPEC[start:end].splitlines()
                   if ln.startswith("cf informalize init ")]
        flags = {flag: value.strip('"') for flag, value in
                 re.findall(r'--([a-z-]+) ("[^"]*"|\S+)', line)}
        outside = SPEC[:start] + SPEC[end:]
        parts = [c for c in flags["module"].split(".")[1:] if c not in outside]
        return [flags["prefix"], flags["reference"].split(",")[0].split()[-1],
                flags["theorem"].rsplit(".", 1)[-1], *parts]

    def test_no_document_names_the_first_target(self):
        needles = self.needles()
        self.assertTrue(needles, "the acceptance section names nothing particular")
        for name in self.DOCUMENTS:
            text = read(name)
            for needle in needles:
                with self.subTest(document=name, needle=needle):
                    self.assertNotIn(needle, text)


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
