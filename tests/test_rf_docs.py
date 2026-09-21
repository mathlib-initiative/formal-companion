"""The docs-to-CLI contract: a command or a path a document names must exist.

Four review findings were one defect. `SKILL.md` told a model to run
`cf harvest <run-id>` while the gate was opt-in and `README.md` passed `--gate`;
`cf run`'s index row promised to "drive the fleet" when the flag that does it
(`--execute`) appeared in no document; `references/runbook-server.md` sent a
stuck reader to `cf ingest <results.json>`, a command that has never existed; and
44 docstring citations pointed at four design documents nobody ever wrote.  The
suite had 1,429 passing tests and could not see any of it: the two SKILL.md
checks that existed (``tests/test_integration.py``) hard-code the command list
and read no flags at all.

So this module parses the documents and asks the real argparse tree:

* every ``cf <command> [<sub>]`` written in a fenced block or in backticks
  parses -- or is labelled on the same line as deleted / not implemented;
* every ``--flag`` written beside such a command belongs to that command, and a
  flag named on its own in the two entry documents is a flag of *something*;
* every command the CLI registers has a row in SKILL.md's command index, and
  every ``sources <sub>`` row of that index parses;
* every repo-relative path a document cites resolves on disk, every relative
  markdown link resolves, and no ``design/…`` citation in a code docstring
  points at a document that is not in the tree.

The point is drift.  This repo is meant to run over many books, so the index,
the flags and the layout will all move again -- and a document is what a fresh
session reads *instead of* the code, which makes a wrong one worse than a
missing one.
"""
from __future__ import annotations

import argparse
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


# ---------------------------------------------------------------------------
# the real CLI surface
# ---------------------------------------------------------------------------

def cli_tree() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """({command: flags}, {"<command> <sub>": flags}) from the live parsers.

    Built by registering every ``cfcmd`` module into a throwaway parser, so the
    answer is what this checkout accepts rather than a list retyped here.
    """
    parser = argparse.ArgumentParser(prog="cf")
    subparsers = parser.add_subparsers(dest="command")
    cf_main.discover_commands(subparsers)
    commands: dict[str, set[str]] = {}
    subcommands: dict[str, set[str]] = {}
    for name, sub in sorted(subparsers.choices.items()):
        commands[name] = _option_strings(sub)
        for action in sub._actions:                          # noqa: SLF001 - no public API
            if isinstance(action, argparse._SubParsersAction):    # noqa: SLF001
                for child, child_parser in action.choices.items():
                    subcommands[f"{name} {child}"] = _option_strings(child_parser)
    return commands, subcommands


def _option_strings(parser) -> set[str]:
    return {opt for action in parser._actions                # noqa: SLF001
            for opt in action.option_strings if opt.startswith("--")}


COMMANDS, SUBCOMMANDS = cli_tree()
#: for a flag named in prose with no command beside it.
ALL_FLAGS = {flag for flags in COMMANDS.values() for flag in flags}
ALL_FLAGS |= {flag for flags in SUBCOMMANDS.values() for flag in flags}
#: argparse gives every parser these, and `--workspace` is the global.
GLOBAL_FLAGS = {"--help", "--version", "--workspace"}
ALL_FLAGS |= GLOBAL_FLAGS

#: A command may be named as absent -- `cf book` was deleted and `fix` is v1
#: out-of-scope -- but only where the same line says so.  A stale command with no
#: such label is `cf ingest`, and that is the defect this catches.
ABSENT_LABELS = ("deleted", "not implemented", "out of scope", "never existed",
                 "does not exist", "no such command", "replaced by", "replaces")


# ---------------------------------------------------------------------------
# the documents
# ---------------------------------------------------------------------------

def _existing(*names: str) -> list[str]:
    return [n for n in names if (SKILL / n).exists()]


DOCS = _existing("SKILL.md", "README.md", "CLAUDE.md", "CONTRIBUTING.md",
                 "POLICY.md", "CHANGELOG.md", "templates/PRESETS.md",
                 "registry/README.md", "evals/README.md")
DOCS += [f"references/{p.name}" for p in sorted((SKILL / "references").glob("*.md"))]
DOCS += [f"docs/design/{p.name}" for p in sorted((SKILL / "docs" / "design").glob("*.md"))]

#: `docs/design/archive/` is deliberately NOT in DOCS.  An archived design record
#: describes the CLI and the tree of the day it was written -- that is what
#: archiving it means, and its banner names what superseded it, which
#: :class:`TestTheArchivedSpecSaysSo` checks instead.
ARCHIVE = SKILL / "docs" / "design" / "archive"

#: A bare `--flag` in prose is checked in the two documents a fresh session
#: actually reads.  Elsewhere the flags belong to the gate, to `tend`, to `lake`
#: or to a companion's own scripts, and a whitelist of those would be longer than
#: the check is worth.
PROSE_FLAG_DOCS = ("SKILL.md", "README.md")
#: flags of other programs that the two entry documents legitimately name.
FOREIGN_FLAGS = {"--no-verify", "--porcelain", "--offline", "--fixtures",
                 "--group-by", "--changed-files", "--base", "--gate-config",
                 "--include-statements", "--full-quotes"}
#: a line starting with one of these is another program's command line.
OTHER_PROGRAM = re.compile(
    r"^\s*(git|gh|tend|tend-task|tend-control|ssh|rsync|bash|sh|python3?|pip|"
    r"brew|apt-get|ln|mkdir|cd|ls|tail|grep|wc|find|sed|awk|cat|open|"
    r"pdftotext|pdftoppm|curl|lake|npm|node|export|CF=)\b")


def read(name: str) -> str:
    return (SKILL / name).read_text(encoding="utf-8")


#: `cf …`, `python3 $CF …`, `python3 scripts/cf.py …`.  The `python3` (or the
#: bare shim at the start of the span) is required, so README's file-tree line
#: "scripts/cf.py     the workspace CLI" is not read as an invocation.
_INVOCATION = re.compile(
    r"""^\s*(?:\$\s*)?
        (?:python3?\s+(?:\S*cf\.py|\$CF|\$\{CF\})|cf)
        \s+(?P<rest>[a-z][a-z0-9-]*(?:\s.*)?)$""",
    re.X)

_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
#: fences holding another file's content, not citations of this repo
_DATA_FENCE = ("yaml", "json", "js", "python", "lean", "diff")


def code_spans(text: str, *, data_fences: bool = True) -> list[str]:
    """Every fenced-block line and every inline-code span, one per entry."""
    spans: list[str] = []
    fence = None
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if stripped.startswith("```"):
            fence = None if fence is not None else (stripped[3:].strip() or "sh")
            continue
        if fence is not None:
            if data_fences or fence not in _DATA_FENCE:
                spans.append(raw)
        else:
            spans.extend(re.findall(r"`([^`\n]+)`", raw))
    return spans


def invocations(text: str) -> list[tuple[str, list[str], str]]:
    """[(command path, flags on the line, the line itself)] for each `cf …`."""
    found = []
    for span in code_spans(text):
        line = span.split("#", 1)[0].strip()
        match = _INVOCATION.match(line)
        if not match:
            continue
        rest = match.group("rest")
        words = [w for w in rest.split() if w]
        path = words[0]
        if len(words) > 1 and f"{path} {words[1]}" in SUBCOMMANDS:
            path = f"{path} {words[1]}"
        found.append((path, _FLAG.findall(rest), span))
    return found


def _blocks_naming(text: str, needle: str) -> str:
    """Every blank-line-separated block that names ``needle``, lowercased.

    A block, not a line: "`cf lint` (specified in references/review-patterns.md,
    not implemented in v1)" wraps, and the label lands on the second line.
    """
    blocks = re.split(r"\n\s*\n", text)
    return " ".join(b for b in blocks if needle in b).lower()


def inline_spans_by_line(text: str) -> list[tuple[str, list[str]]]:
    """[(the prose line, its inline-code spans)] outside fenced blocks.

    The line is kept because a flag is often named in order to say it is gone:
    README's "No `--book`, no `--pdf`, no `--offset`" is the whole point of the
    sentence it sits in.
    """
    out: list[tuple[str, list[str]]] = []
    fenced = False
    for raw in text.splitlines():
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            out.append((raw, [raw]))
        else:
            spans = re.findall(r"`([^`\n]+)`", raw)
            if spans:
                out.append((raw, spans))
    return out


class TestEveryDocumentedCommandExists(unittest.TestCase):
    """`cf ingest <results.json>` sat in the "what stays manual" list."""

    def test_every_cf_command_written_in_a_document_parses(self):
        for name in DOCS:
            text = read(name)
            for path, _flags, line in invocations(text):
                head = path.split(" ")[0]
                with self.subTest(doc=name, command=path):
                    if head in COMMANDS:
                        if " " in path:
                            self.assertIn(path, SUBCOMMANDS, f"{name}: {line}")
                        continue
                    # not a command: wherever it is named, the text must say so.
                    context = _blocks_naming(text, f"cf {head}")
                    self.assertTrue(any(w in context for w in ABSENT_LABELS),
                                    f"{name}: `cf {head}` is not a command and nothing "
                                    f"beside it says it is gone ({line})")

    def test_every_flag_beside_a_command_belongs_to_it(self):
        for name in DOCS:
            for path, flags, line in invocations(read(name)):
                known = COMMANDS.get(path.split(" ")[0], set()) | SUBCOMMANDS.get(path, set())
                if not known:
                    continue
                for flag in flags:
                    with self.subTest(doc=name, command=path, flag=flag):
                        self.assertIn(flag, known | GLOBAL_FLAGS | FOREIGN_FLAGS,
                                      f"{name}: `cf {path}` has no {flag} ({line})")

    def test_every_flag_named_in_prose_is_a_flag_of_something(self):
        """`--gate`, `--no-gate` and `--execute` are named on their own in SKILL.md."""
        for name in PROSE_FLAG_DOCS:
            for line, spans in inline_spans_by_line(read(name)):
                # "No `--book`, no `--pdf`, no `--offset`" names them to say they
                # are gone, which is the R2 promise, not a stale flag.
                if re.search(r"\bno\s+`--|never|deleted|no longer", line, re.I):
                    continue
                for span in spans:
                    stripped = span.split("#", 1)[0].strip()
                    if _INVOCATION.match(stripped) or OTHER_PROGRAM.match(stripped):
                        continue
                    for flag in _FLAG.findall(stripped):
                        if flag in FOREIGN_FLAGS:
                            continue
                        with self.subTest(doc=name, flag=flag):
                            self.assertIn(flag, ALL_FLAGS,
                                          f"{name}: {flag} is no flag of any command ({span})")


class TestTheCommandIndexIsComplete(unittest.TestCase):
    """A command absent from the index is a command a fresh session never runs."""

    def index_rows(self) -> set[str]:
        return set(re.findall(r"^\|\s*`([a-z-]+)[^`]*`", read("SKILL.md"), re.M))

    def test_every_registered_command_is_in_the_index(self):
        missing = sorted(set(COMMANDS) - self.index_rows())
        self.assertEqual(missing, [], f"SKILL.md's command index is missing: {missing}")

    def test_the_index_invents_no_command(self):
        invented = sorted(self.index_rows() - set(COMMANDS))
        self.assertEqual(invented, [], f"SKILL.md's index names non-commands: {invented}")

    def test_every_sources_subcommand_row_parses(self):
        documented = set(re.findall(r"`sources ([a-z-]+)", read("SKILL.md")))
        self.assertIn("promote", documented, "cf sources promote is undocumented")
        for sub in sorted(documented):
            with self.subTest(sub=sub):
                self.assertIn(f"sources {sub}", SUBCOMMANDS)


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

#: A token is read as a path in THIS repo when it starts with one of these.
#: Anything else -- `~/Downloads`, `tao-measure-lean/docs/HANDOFF.md`,
#: `<workspace>/registry.yaml` -- is another tree's and is not checked.
REPO_ROOTS = ("scripts/", "references/", "docs/", "templates/", "tests/",
              "registry/", "workflows/", "evals/", ".github/")
REPO_FILES = ("SKILL.md", "README.md", "CLAUDE.md", "CONTRIBUTING.md",
              "CHANGELOG.md", "POLICY.md", "LICENSE", "pyproject.toml", ".gitignore")

_PATHISH = re.compile(
    r"[A-Za-z0-9_./*<>-]+\.(?:py|md|sh|js|json|jsonl|yaml|yml|txt|tmpl|toml|example)\b")


def cited_paths(text: str) -> set[str]:
    """Paths in this repo that a document names in backticks or a shell block."""
    found: set[str] = set()
    for span in code_spans(text, data_fences=False):
        for token in _PATHISH.findall(span):
            token = token.strip("`,;:()")
            if token.startswith(REPO_ROOTS) or token in REPO_FILES:
                found.add(token)
    return found


def resolves(token: str) -> bool:
    """Does the path exist?  A `<placeholder>` or `*` glob checks its parent."""
    keep: list[str] = []
    for part in Path(token).parts:
        if any(ch in part for ch in "<*…"):
            break
        keep.append(part)
    if not keep:
        return True
    return (SKILL / Path(*keep)).exists()


class TestEveryCitedRepoPathExists(unittest.TestCase):
    """Five stale paths in the reference docs, and 44 in the docstrings."""

    def test_documents_cite_no_path_that_is_not_here(self):
        for name in DOCS:
            for token in sorted(cited_paths(read(name))):
                with self.subTest(doc=name, path=token):
                    self.assertTrue(resolves(token),
                                    f"{name} cites {token}, which is not in this repo")

    def test_relative_markdown_links_resolve(self):
        for name in DOCS:
            base = (SKILL / name).parent
            for target in re.findall(r"\]\(([^)\s]+)\)", read(name)):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                with self.subTest(doc=name, link=target):
                    self.assertTrue((base / target.split("#")[0]).exists(),
                                    f"{name}: the link {target} resolves nowhere")

    def test_no_code_docstring_cites_a_design_document_that_was_never_written(self):
        """18x stage_map.md, 17x critique.json, 7x architecture.md, 2x decisions.json."""
        broken: list[str] = []
        sources = sorted(SKILL.glob("scripts/**/*.py")) + sorted(SKILL.glob("tests/*.py"))
        for path in sources:
            text = path.read_text(encoding="utf-8")
            cited = set(re.findall(r"(?<![\w/-])((?:docs/)?design/[a-z_/-]+\.(?:md|json))", text))
            for token in cited:
                if not (SKILL / token).exists():
                    broken.append(f"{path.relative_to(SKILL)}: {token}")
        self.assertEqual(sorted(broken), [],
                         "design documents cited but absent: " + "; ".join(sorted(broken)))


class TestTheArchivedSpecSaysSo(unittest.TestCase):
    """A document that calls itself binding must not be the stale one."""

    def test_the_archive_banner_names_what_superseded_it(self):
        archived = sorted(ARCHIVE.glob("*.md"))
        self.assertTrue(archived, "docs/design/archive/ holds no archived design record")
        for path in archived:
            head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:30]).lower()
            with self.subTest(archived=path.name):
                self.assertIn("historical", head)
                self.assertIn("docs/design/stage_map.md", head)
                self.assertNotIn("**status:** binding", head)

    def test_only_the_stage_map_calls_itself_current(self):
        current = sorted((SKILL / "docs" / "design").glob("*.md"))
        self.assertEqual([p.name for p in current], ["stage_map.md"],
                         "docs/design/ holds a second document claiming to be current")


class TestTheEntryPointStatesItsPreconditions(unittest.TestCase):
    """The single biggest barrier to a second user was `<skill>`, unresolved."""

    def test_skill_md_names_the_prerequisites_and_points_at_the_readme(self):
        text = read("SKILL.md")
        for needle in ("3.11", "pdftotext", "README.md", "cf doctor"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_skill_md_stays_under_the_length_guideline(self):
        lines = read("SKILL.md").splitlines()
        self.assertLess(len(lines), 500, "SKILL.md is meant to stay under ~500 lines: "
                                         "move detail into references/")

    def test_the_default_program_gates_the_corpus(self):
        """Following the program literally must not produce an ungated corpus."""
        program = read("SKILL.md")
        start = program.index("## The default program")
        end = program.index("## Command index")
        self.assertRegex(program[start:end], r"gate",
                         "the default program never mentions the findings gate")

    def test_the_workflow_stages_are_named_somewhere_in_the_skill(self):
        text = read("SKILL.md")
        self.assertIn("--emit-args", text)
        self.assertIn("--execute", text)
        self.assertIn("scriptPath", text)

    def test_every_long_reference_carries_a_table_of_contents(self):
        for path in sorted((SKILL / "references").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            if len(text.splitlines()) < 150:
                continue
            with self.subTest(reference=path.name):
                self.assertRegex(text, r"(?m)^\*\*Contents\*\*|^## Contents",
                                 f"{path.name} is over 150 lines and carries no TOC")

    def test_the_references_list_points_at_documents_that_exist(self):
        listed = re.findall(r"^- `([^`]+\.md)`", read("SKILL.md"), re.M)
        self.assertTrue(listed, "SKILL.md has no References list any more")
        for name in listed:
            with self.subTest(reference=name):
                self.assertTrue((SKILL / name).exists(),
                                f"SKILL.md lists {name}, which is absent")


# ---------------------------------------------------------------------------
# the registry: the one file a contributor writes by hand
# ---------------------------------------------------------------------------

import os                                                     # noqa: E402
import shutil                                                 # noqa: E402
import tempfile                                               # noqa: E402

from cflib.registry import metadata_problems, skill_registry_path   # noqa: E402

SOURCE_ID = "anonymous-analysis-2e"
SHA = "a1" * 32


def _scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(dir=os.environ.get("CF_TEST_TMP") or None))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path.resolve()


def _workspace(case: unittest.TestCase, *, confirmed: bool = True) -> Path:
    """A workspace holding one discovered source, with an overlay row for it."""
    root = _scratch(case) / "ws"
    root.mkdir(parents=True)
    (root / "workspace.yaml").write_text(
        "version: 2\n"
        "workspace:\n  name: probe\n"
        "companions: {}\n"
        "sources:\n"
        f"  {SOURCE_ID}:\n"
        f'    sha256: "{SHA}"\n'
        "    title: An Introduction to Analysis\n"
        '    edition: "second edition (published pagination)"\n'
        "    offset: 16\n"
        "    role: audited\n"
        f"    confirmed: {'true' if confirmed else 'false'}\n"
        "hosts:\n  allowlist: []\n", encoding="utf-8")
    (root / "registry.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        f"  {SOURCE_ID}:\n"
        "    title: An Introduction to Analysis\n"
        '    edition: "second edition (published pagination)"\n'
        f'    sha256: "{SHA}"\n'
        "    licence: {note: public domain, fetch: open, redistribute: false}\n"
        f"    confirmed: {'true' if confirmed else 'false'}\n", encoding="utf-8")
    return root


def _cf(*argv: str) -> int:
    """Run the CLI in-process; CfError becomes its exit code, as `cli()` does."""
    from cflib.errors import CfError
    try:
        return int(cf_main.main(list(argv)) or 0)
    except CfError as exc:
        return int(getattr(exc, "exit_code", 2) or 2)


class TestTheRegistryIsCheckedMechanically(unittest.TestCase):
    """`Registry.validate()` existed and nothing ran it on the shipped file."""

    def test_the_shipped_registry_passes_the_metadata_only_check(self):
        self.assertEqual(metadata_problems(skill_registry_path()), [])

    def test_cf_sources_validate_registry_needs_no_workspace(self):
        # CI runs it in a clone: a command that loaded a workspace could not.
        cwd = os.getcwd()
        os.chdir(_scratch(self))
        try:
            self.assertEqual(_cf("sources", "validate-registry"), 0)
        finally:
            os.chdir(cwd)

    def test_a_passage_of_the_book_in_an_entry_is_refused(self):
        path = _scratch(self) / "sources.yaml"
        path.write_text(
            "version: 1\n"
            "sources:\n"
            "  a-book-1e:\n"
            "    title: A Book\n"
            "    edition: first\n"
            "    excerpt: >\n"
            "      Let E be a subset of the reals. We say that E is elementary if it\n"
            "      can be expressed as the finite union of intervals, and its measure\n"
            "      is then the sum of their lengths.\n", encoding="utf-8")
        problems = metadata_problems(path)
        self.assertTrue(any("unknown key `excerpt`" in p for p in problems), problems)
        self.assertTrue(any("never a passage of it" in p for p in problems), problems)

    def test_a_second_sentence_outside_the_free_text_fields_is_refused(self):
        path = _scratch(self) / "sources.yaml"
        path.write_text(
            "version: 1\n"
            "sources:\n"
            "  a-book-1e:\n"
            '    edition: "first. And here is the rest of the page."\n', encoding="utf-8")
        self.assertTrue(any("second sentence" in p for p in metadata_problems(path)),
                        metadata_problems(path))

    def test_the_free_text_fields_may_hold_a_sentence(self):
        path = _scratch(self) / "sources.yaml"
        path.write_text(
            "version: 1\n"
            "sources:\n"
            "  a-book-1e:\n"
            '    notes: "The edition to audit against. Both companions anchor to it."\n'
            "    licence: {note: 'AMS copyright. The author posts a free preliminary "
            "version on his own site.', fetch: author_site, redistribute: false}\n",
            encoding="utf-8")
        self.assertEqual([p for p in metadata_problems(path) if "sentence" in p], [])


class TestTheTriggerEvalsAreWellFormed(unittest.TestCase):
    """Data, not a test: nothing in tests/ may call a model.

    The eval set is what measures the one thing 1,400 unit tests cannot -- does
    the skill fire when it should, and stay out of the way when it should not.
    Checking its shape here is free and catches the failure that wastes a real
    eval run: a malformed set that only fails after N model calls.
    """

    def setUp(self):
        import json
        self.cases = json.loads(read("evals/trigger.json"))

    def test_it_is_the_shape_the_runner_reads(self):
        self.assertIsInstance(self.cases, list)
        seen = set()
        for case in self.cases:
            with self.subTest(case=case.get("id")):
                self.assertIsInstance(case.get("query"), str)
                self.assertTrue(case["query"].strip())
                self.assertIsInstance(case.get("should_trigger"), bool)
                self.assertTrue(case.get("why"), "every case says what it protects")
                self.assertNotIn(case["query"], seen, "duplicate query")
                seen.add(case["query"])

    def test_both_directions_are_measured(self):
        positives = [c for c in self.cases if c["should_trigger"]]
        negatives = [c for c in self.cases if not c["should_trigger"]]
        self.assertGreaterEqual(len(positives), 2)
        self.assertGreaterEqual(len(negatives), 2, "the negatives are the half that "
                                                   "measures over-triggering")

    def test_no_test_module_calls_a_model(self):
        for path in sorted(SKILL.glob("tests/*.py")):
            # this module is the one place those strings are allowed: it names
            # them in order to look for them.
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for needle in ("claude -p", "run_eval.py", "anthropic"):
                    self.assertNotIn(needle, text,
                                     f"{path.name} looks like it calls a model")


class TestPromoteWritesAPatchAndNotTheShippedRegistry(unittest.TestCase):
    """Contributing an edition was four prose steps and no command."""

    def test_it_writes_a_patch_file_and_leaves_the_registry_alone(self):
        root = _workspace(self)
        before = skill_registry_path().read_bytes()
        self.assertEqual(_cf("--workspace", str(root), "sources", "promote", SOURCE_ID), 0)
        patch = root / f"registry-patch.{SOURCE_ID}.yaml"
        self.assertTrue(patch.exists(), "no patch file was written")
        body = patch.read_text(encoding="utf-8")
        self.assertIn(SOURCE_ID, body)
        self.assertIn("confirmed: true", body)
        self.assertEqual(skill_registry_path().read_bytes(), before,
                         "promote wrote the shipped registry from a workspace")
        self.assertEqual(metadata_problems(patch), [])

    def test_it_refuses_a_source_nobody_confirmed(self):
        root = _workspace(self, confirmed=False)
        self.assertEqual(_cf("--workspace", str(root), "sources", "promote", SOURCE_ID), 2)
        self.assertFalse((root / f"registry-patch.{SOURCE_ID}.yaml").exists())

    def test_it_refuses_an_id_the_skill_already_ships(self):
        root = _workspace(self)
        shipped = sorted(__import__("cflib.registry", fromlist=["Registry"])
                         .Registry.load(None).entries)
        self.assertEqual(_cf("--workspace", str(root), "sources", "promote", shipped[0]), 2)


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
