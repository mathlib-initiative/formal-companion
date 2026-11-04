"""`cf informalize init | sync | status | check-items | approve | reference-checklist`.

New code, for docs/proposals/informalize.md section 11, component 3.  Four groups:

* the formats and their rules, without a workspace: `informalize.yaml` (section 5.1), the
  item table (5.4) and `declarations.json` (5.3), each on the specification's own example
  and on broken copies; the JSON Schemas under templates/informalize/schema/ against the
  modules that implement them; the vocabulary and the id grammar;
* `check-items` against a fake library: the declarations of tests/_informalize_doc.py, and
  a source file whose keyword lines hold them;
* the commands in a temporary workspace: init, the repository it creates and the one
  exception it writes under, sync and `sync --check`, the five license texts, the item
  approval with its renumbering and its frozen ids, the reference checklist and its
  approval, status, and the gates whose stages are later components';
* the repository `init` renders, built with the runtime once the approval has written its
  sections (TeX only; skipped without it, a failure under CF_REQUIRE_TEX=1).

No Lean runs: the library is a fake source tree and a fake declarations.json.
"""
from __future__ import annotations

# Pin HOME before anything builds a workspace: `unittest discover` loads no
# conftest.py.  The second spelling is for `python3 -m unittest tests.<module>`.
# See tests/_hermetic.py.
try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import copy
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
for entry in (str(HERE), str(SKILL / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import _informalize_doc as doc                                # noqa: E402
import cf as cf_main                                          # noqa: E402
from cflib import items as cfitems                            # noqa: E402
from cflib import vocab                                       # noqa: E402
from cflib.errors import CfError                              # noqa: E402
from cflib.paths import WorkspacePaths                        # noqa: E402
from cflib.yamlio import load_yaml, read_yaml                 # noqa: E402
from informalize import config, itemtable, project, runtime   # noqa: E402
from informalize.approve import AGENT_ENVIRONMENT             # noqa: E402
from informalize.outrepo import GIT_LOCATION_ENV, OutputRepo  # noqa: E402
from overlay.config import OPEN_LICENSES                      # noqa: E402

SPEC = (SKILL / "docs" / "proposals" / "informalize.md").read_text(encoding="utf-8")
SCHEMAS = SKILL / "templates" / "informalize" / "schema"
ID = "example-thm"
URL = doc.SLOTS["library_url"]
INIT = ["--library", URL, "--commit", doc.COMMIT, "--module", "ExampleLib.Topology.Glue.Basic",
        "--theorem", "ExampleLib.glue_theorem", "--prefix", "ExampleLib",
        "--text-license", "Apache-2.0", "--title", "The glue theorem, after ExampleLib",
        "--author", "A. Maintainer", "--reference", "A. Author, Gluing, Example Press, 2001"]
#: the keyword line of each fake declaration, at the line tests/_informalize_doc.py gives it
SOURCE_LINES = {
    12: "structure GlueData (X : Type*) [TopologicalSpace X] where",
    30: "def glue_map {X : Type*} [TopologicalSpace X] (U V : Set X) : Glued X :=",
    40: "theorem glue_theorem {X : Type*} [TopologicalSpace X] (U V : Set X)",
    52: "theorem glue_surjective {X : Type*} [TopologicalSpace X] (U V : Set X)",
    60: "lemma cover_mono {X : Type*} {U V W : Set X} (h : U ⊆ V) :",
    66: "theorem cover_univ {X : Type*} (U : Set X) : Covers Set.univ U := by",
    72: "@[simp] theorem glue_card {X : Type*} [TopologicalSpace X] {U V : Set X}",
    80: "theorem chain_long (n : ℕ) : chain n := by",
    90: "protected theorem ExampleLib.chain_short (n : ℕ) : chain (n + 1) → chain n := by",
}
SOURCE_PATH = "ExampleLib/Topology/Glue/Basic.lean"


def spec_examples() -> dict[str, dict]:
    out = {}
    for body in re.findall(r"^```json\n(.*?)^```", SPEC, re.M | re.S):
        data = json.loads(body)
        out[data["schema"]] = data
    return out


def spec_yaml() -> dict:
    (body,) = re.findall(r"^```yaml\n(.*?)^```", SPEC, re.M | re.S)
    return load_yaml(body, prefer_pyyaml=False)


def cf(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cf_main.cli(list(argv))
        except SystemExit as exc:          # argparse
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def write_library(src: Path) -> None:
    lines = [f"-- line {n}" for n in range(1, 101)]
    for number, text in SOURCE_LINES.items():
        lines[number - 1] = text
    path = src / SOURCE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def declarations_bytes() -> bytes:
    return (json.dumps(doc.DECLARATIONS, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def numbered_table() -> dict:
    """tests/_informalize_doc.py's table, against the declarations.json written here."""
    table = copy.deepcopy(doc.ITEMS)
    table["library"]["declarations_sha256"] = hashlib.sha256(declarations_bytes()).hexdigest()
    return table


def provisional_table() -> dict:
    """The table as the items task drafts it: provisional ids with gaps, a third section
    whose only item stays out of version 1, and the not-in-v1 remark of section 2."""
    table = numbered_table()
    renames = {"1.2": "1.4", "2.1": "2.2", "2.2": "2.5", "2.3": "2.7", "2.4": "2.9"}
    for item in table["items"]:
        item["id"] = renames.get(item["id"], item["id"])
    table["sections"].append({"number": "3", "title": "Later", "slug": "later"})
    table["items"].append({"id": "3.1", "section": "3", "kind": "remark", "title": "Later",
                           "gist": "Left for later.", "parts": [], "in_v1": True,
                           "reference": None, "departures": "",
                           "declarations": [{"name": "ExampleLib.chain_long",
                                             "module": "ExampleLib.Topology.Glue.Basic",
                                             "line": 80, "part": None, "role": "key",
                                             "relation": "exactMatch", "junk": [],
                                             "hypotheses": []}]})
    return table


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-informalize-", dir=os.environ.get("CF_TEST_TMP")))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


# ---------------------------------------------------------------------------
# the formats, without a workspace

class TestTheVocabulary(unittest.TestCase):

    def test_the_six_enums_are_the_specification_s(self):
        self.assertEqual(vocab.INFORMALIZE_KINDS, (
            "definition", "lemma", "proposition", "theorem", "corollary", "example", "remark"))
        self.assertEqual(vocab.INFORMALIZE_RELATIONS,
                         ("exactMatch", "broadMatch", "narrowMatch", "relatedMatch"))
        for name in ("INFORMALIZE_KINDS", "INFORMALIZE_ROLES", "INFORMALIZE_RELATIONS",
                     "REFERENCE_SOURCES", "INFORMALIZE_PHASES", "APPROVAL_GATES"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", (SKILL / "references" / "vocabulary.md")
                              .read_text(encoding="utf-8"))

    def test_an_item_id_is_two_numbers_from_1(self):
        for good in ("1.1", "2.10", "12.3"):
            self.assertTrue(cfitems.is_informal_item_id(good))
        for bad in ("0.1", "1.0", "01.2", "1.02", "1.2a", "1.2(a)", " 1.2", "1", "1.2.3", 1.2):
            with self.subTest(id=bad):
                self.assertFalse(cfitems.is_informal_item_id(bad))
        self.assertEqual(cfitems.split_informal_item_id("2.3"), (2, 3))
        self.assertEqual(cfitems.informal_item_id(2, 3), "2.3")
        with self.assertRaises(CfError):
            cfitems.split_informal_item_id("2.03")
        with self.assertRaises(CfError):
            cfitems.informal_item_id(0, 1)

    def test_the_paths_stay_in_the_workspace(self):
        paths = WorkspacePaths(Path("/w"))
        self.assertEqual(paths.informalize_repo(ID), Path("/w/informalize/example-thm/repo"))
        self.assertEqual(paths.informalize_approval(ID, "proof-sheets"),
                         Path("/w/informalize/example-thm/stages/approve-proof-sheets.json"))
        for bad in ("../x", "a/b", "", "."):
            with self.subTest(id=bad), self.assertRaises(CfError):
                paths.informalize_dir(bad)

    def test_cf_init_ignores_the_three_directories(self):
        from cfcmd.init import GITIGNORE
        from informalize.bootstrap import WORKSPACE_IGNORES
        for line in WORKSPACE_IGNORES:
            self.assertIn(f"\n{line}\n", GITIGNORE)
        self.assertIn("`informalize/*/repo/`, `informalize/*/library/` and\n`informalize/*/pdf/`",
                      SPEC)


class TestTheSettings(unittest.TestCase):

    def test_the_specification_s_example_keeps_every_rule(self):
        self.assertEqual(config.problems(spec_yaml()), [])

    def test_init_builds_what_the_example_shows(self):
        example = spec_yaml()
        built = config.build(
            iid=example["id"], url=example["library"]["url"], commit=example["library"]["commit"],
            module=example["library"]["module"], theorem=example["library"]["theorem"],
            prefix=example["library"]["prefix"], text_license=example["text"]["license"],
            title=example["text"]["title"], author=example["text"]["author"],
            reference=example["reference"]["work"], max_cost_per_phase=300)
        self.assertEqual(built, example)
        self.assertEqual(list(built), list(example))

    def test_what_init_writes_reads_back_without_pyyaml(self):
        settings = config.build(iid=ID, url=URL, commit=doc.COMMIT, module="A.B", theorem="A.c",
                                prefix="A", text_license="MIT", title="A title: with a colon",
                                author="O'Brien", library_license="Apache-2.0",
                                library_cache=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "informalize.yaml"
            config.write(path, settings)
            self.assertEqual(read_yaml(path, prefer_pyyaml=False), settings)
        self.assertEqual(settings["library"]["license_method"], "manual")

    def test_a_broken_file_is_refused(self):
        good = spec_yaml()
        breaks = {
            "an id with capitals": lambda d: d.update(id="Example"),
            "an id of 41 characters": lambda d: d.update(id="a" * 41),
            "an http URL": lambda d: d["library"].update(url="http://example.org/x"),
            "a URL with a space": lambda d: d["library"].update(url="https://example.org/a b"),
            "a short commit": lambda d: d["library"].update(commit="0123abc"),
            "a module outside the prefix": lambda d: d["library"].update(prefix="OtherLib"),
            "a closed license": lambda d: d["text"].update(license="CC-BY-NC-4.0"),
            "an unknown key": lambda d: d.update(host="box"),
            "a model in the caps": lambda d: d["caps"].update(model="m"),
            "too many sessions": lambda d: d["caps"].update(workers=14),
            "a timeout past the bridge's": lambda d: d["caps"].update(agent_timeout="9h"),
            "a free spend cap": lambda d: d["caps"].update(max_cost_per_phase=0),
            "a two-line title": lambda d: d["text"].update(title="a\nb"),
            "a manual license without one": lambda d: d["library"].update(license_method="manual"),
            "a string for the cache switch": lambda d: d["library"].update(library_cache="no"),
        }
        for what, change in breaks.items():
            with self.subTest(broken=what):
                broken = copy.deepcopy(good)
                change(broken)
                self.assertTrue(config.problems(broken), what)


class TestTheItemTable(unittest.TestCase):

    def test_the_specification_s_example_keeps_every_rule(self):
        self.assertEqual(itemtable.items_problems(
            spec_examples()["formal-companion/informalize-items"]), [])

    def test_the_runtime_s_synthetic_table_keeps_every_rule(self):
        # an item out of version 1 needs no key declaration
        self.assertEqual(itemtable.items_problems(numbered_table()), [])

    def test_a_provisional_table_may_have_gaps_a_numbered_one_not(self):
        table = provisional_table()
        self.assertEqual(itemtable.items_problems(table, numbered=False), [])
        found = itemtable.items_problems(table)
        self.assertTrue(any("without gaps" in p for p in found), found)

    def test_a_broken_table_is_refused(self):
        good = numbered_table()
        breaks = {
            "a gap in the ids": lambda d: d["items"][1].update(id="1.3"),
            "an id of another section": lambda d: d["items"][1].update(section="2"),
            "a key in no part": lambda d: d["items"][1]["declarations"][0].update(part=None),
            "a part without a key": lambda d: d["items"][1]["declarations"][1].update(role="helper"),
            "parts out of order": lambda d: d["items"][1]["parts"].reverse(),
            "an unknown relation": lambda d: d["items"][0]["declarations"][0].update(relation="sameAs"),
            "a closeMatch": lambda d: d["items"][0]["declarations"][0].update(relation="closeMatch"),
            "an unexplained broadMatch": lambda d: d["items"][0]["declarations"][0].update(
                relation="broadMatch"),
            "an unknown reference source": lambda d: d["items"][0]["reference"].update(source="book"),
            "a docstring number with no declaration": lambda d: d["items"][0]["reference"].update(
                declaration=None),
            "an unknown kind": lambda d: d["items"][0].update(kind="axiom"),
            "an unknown key": lambda d: d["items"][0].update(notes="x"),
            "a declaration twice": lambda d: d["items"][0]["declarations"].append(
                copy.deepcopy(d["items"][0]["declarations"][0])),
            "a slug with a space": lambda d: d["sections"][0].update(slug="gluing spaces"),
            "sections out of order": lambda d: d["sections"].reverse(),
            "an id twice": lambda d: d["items"].append(copy.deepcopy(d["items"][0])),
            "a short sha": lambda d: d["library"].update(declarations_sha256="abc"),
            "a withdrawn id in use": lambda d: d["decisions"].append({"withdrawn": "1.2",
                                                                      "why": "merged"}),
        }
        for what, mutate in breaks.items():
            with self.subTest(broken=what):
                table = copy.deepcopy(good)
                mutate(table)
                self.assertTrue(itemtable.items_problems(table), what)

    def test_an_extension_key_is_allowed(self):
        table = numbered_table()
        table["x_note"] = "kept"
        table["items"][0]["x_checked"] = False
        self.assertEqual(itemtable.items_problems(table), [])

    def test_a_withdrawn_id_stays_a_gap(self):
        table = numbered_table()
        table["items"] = [it for it in table["items"] if it["id"] != "2.2"]
        self.assertTrue(itemtable.items_problems(table))
        table["decisions"].append({"withdrawn": "2.2", "why": "merged into 2.1"})
        self.assertEqual(itemtable.items_problems(table), [])

    def test_renumbering_defers_drops_and_records(self):
        table = provisional_table()
        chosen = {"1.1", "1.4", "2.2", "2.5", "2.7"}
        new, decision = itemtable.renumber(table, chosen)
        self.assertEqual([it["id"] for it in new["items"]], ["1.1", "1.2", "2.1", "2.2", "2.3"])
        self.assertTrue(all(it["in_v1"] for it in new["items"]))
        self.assertEqual([it["id"] for it in new["deferred"]], ["2.9", "3.1"])
        self.assertEqual([s["number"] for s in new["sections"]], ["1", "2"])
        self.assertEqual(decision["renumbered"], {"1.4": "1.2", "2.2": "2.1", "2.5": "2.2",
                                                  "2.7": "2.3"})
        self.assertEqual(decision["dropped_sections"], ["3"])
        self.assertEqual(itemtable.items_problems(new), [])
        self.assertEqual(itemtable.sections_tex(new), doc.SECTIONS_TEX)
        again, nothing = itemtable.renumber(new, {it["id"] for it in new["items"]})
        self.assertEqual((again["items"], nothing), (new["items"], {}))

    def test_a_dropped_middle_section_renumbers_the_rest(self):
        table = provisional_table()
        new, decision = itemtable.renumber(table, {"1.1", "1.4", "3.1"})
        self.assertEqual([(s["number"], s["slug"]) for s in new["sections"]],
                         [("1", "gluing-spaces"), ("2", "later")])
        self.assertEqual(new["items"][-1]["id"], "2.1")
        self.assertEqual(decision["sections"], {"3": "2"})

    def test_frozen_ids_hold(self):
        table = numbered_table()
        before = itemtable.snapshot(table)
        self.assertEqual(itemtable.frozen_problems(table, before), [])
        cases = {
            "a kind changed": lambda d: d["items"][0].update(kind="lemma"),
            "an id gone": lambda d: d["items"].pop(1),
            "a slug changed": lambda d: d["sections"][1].update(slug="cover"),
            "a section slipped in": lambda d: (d["sections"][1].update(number="3"),
                                               d["sections"].insert(1, {"number": "2",
                                                                        "title": "New",
                                                                        "slug": "new"})),
        }
        for what, change in cases.items():
            with self.subTest(change=what):
                changed = copy.deepcopy(table)
                change(changed)
                self.assertTrue(itemtable.frozen_problems(changed, before), what)
        added = copy.deepcopy(table)
        added["items"].append(dict(copy.deepcopy(added["items"][4]), id="2.5"))
        self.assertEqual(itemtable.frozen_problems(added, before), [])
        # an item between two approved ones of its section
        without = copy.deepcopy(table)
        without["items"] = [it for it in without["items"] if it["id"] != "2.2"]
        found = itemtable.frozen_problems(table, itemtable.snapshot(without))
        self.assertTrue(any("comes after every approved item" in p for p in found), found)

    def test_the_reference_rows_are_the_in_v1_numbers(self):
        rows = itemtable.reference_rows(numbered_table())
        self.assertEqual([(r["id"], r["number"], r["source"]) for r in rows],
                         [("1.1", "Definition 2.3", "docstring"), ("1.2", "Theorem 4.1", "inferred"),
                          ("2.3", "Corollary 4.7", "module docstring")])
        text = itemtable.checklist_markdown(ID, "A work", rows)
        self.assertIn(f"List sha256: `{itemtable.rows_sha256(rows)}`", text)
        self.assertEqual(text, itemtable.checklist_markdown(ID, "A work", rows))


class TestTheDeclarations(unittest.TestCase):

    def test_the_synthetic_declarations_keep_every_rule(self):
        self.assertEqual(itemtable.declarations_problems(doc.DECLARATIONS), [])

    def test_broken_declarations_are_refused(self):
        breaks = {
            "unsorted": lambda d: d["declarations"].reverse(),
            "a path that climbs": lambda d: d["declarations"][0].update(path="../x.lean"),
            "an absolute path": lambda d: d["declarations"][0].update(path="/x.lean"),
            "a line outside the span": lambda d: d["declarations"][0].update(line=1),
            "a kind the probe never records": lambda d: d["declarations"][0].update(kind="other"),
            "a name twice": lambda d: d["declarations"].insert(1, d["declarations"][0]),
        }
        for what, change in breaks.items():
            with self.subTest(broken=what):
                data = copy.deepcopy(doc.DECLARATIONS)
                change(data)
                self.assertTrue(itemtable.declarations_problems(data), what)


class TestTheSchemaFiles(unittest.TestCase):
    """The JSON Schemas are for a reader; the modules are the rule. One set of keys,
    enums and patterns."""

    @staticmethod
    def schema(name: str) -> dict:
        return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))

    @staticmethod
    def ecma(pattern: str) -> str:
        return "^" + pattern[2:-2] + "$"

    def test_the_settings_schema_is_the_module_s(self):
        s = self.schema("informalize.schema.json")
        self.assertEqual(tuple(s["properties"]), config.TOP_KEYS)
        library, text, caps = (s["properties"][k]["properties"] for k in ("library", "text", "caps"))
        self.assertEqual(tuple(library), config.LIBRARY_KEYS)
        self.assertEqual(tuple(text), config.TEXT_KEYS)
        self.assertEqual(tuple(caps), config.CAP_KEYS)
        self.assertEqual(tuple(text["license"]["enum"]), OPEN_LICENSES)
        self.assertEqual(s["properties"]["id"]["pattern"], self.ecma(config.ID_PATTERN))
        for key, pattern in (("url", config.URL_PATTERN), ("commit", config.COMMIT_PATTERN),
                             ("module", config.MODULE_PATTERN), ("prefix", config.MODULE_PATTERN),
                             ("theorem", config.DECLARATION_PATTERN),
                             ("license", config.SPDX_PATTERN)):
            with self.subTest(key=key):
                self.assertEqual(library[key]["pattern"], self.ecma(pattern))
        self.assertEqual(s["properties"]["schema"]["const"], config.SCHEMA)

    def test_the_items_schema_is_the_module_s(self):
        s = self.schema("items.schema.json")
        defs = s["$defs"]
        self.assertEqual(tuple(s["properties"]), itemtable.TOP_KEYS)
        self.assertEqual(tuple(s["required"]), itemtable.TOP_KEYS)
        for name, keys in (("section", itemtable.SECTION_KEYS), ("item", itemtable.ITEM_KEYS),
                           ("part", itemtable.PART_KEYS), ("reference", itemtable.REFERENCE_KEYS),
                           ("declaration", itemtable.DECLARATION_KEYS)):
            with self.subTest(definition=name):
                self.assertEqual(tuple(defs[name]["properties"]), keys)
                self.assertEqual(tuple(defs[name]["required"]), keys)
        self.assertEqual(tuple(defs["item"]["properties"]["kind"]["enum"]), vocab.INFORMALIZE_KINDS)
        self.assertEqual(tuple(defs["declaration"]["properties"]["role"]["enum"]),
                         vocab.INFORMALIZE_ROLES)
        self.assertEqual(tuple(defs["declaration"]["properties"]["relation"]["enum"]),
                         vocab.INFORMALIZE_RELATIONS)
        self.assertEqual(tuple(defs["reference"]["properties"]["source"]["enum"]),
                         vocab.REFERENCE_SOURCES)
        self.assertEqual(defs["item"]["properties"]["id"]["pattern"],
                         self.ecma(cfitems.INFORMAL_ITEM_ID.pattern))
        self.assertEqual(defs["section"]["properties"]["slug"]["pattern"],
                         self.ecma(itemtable.SLUG_PATTERN))
        self.assertEqual(s["properties"]["schema"]["const"], itemtable.ITEMS_SCHEMA)


# ---------------------------------------------------------------------------
# check-items against a fake library

class TestTheTableAgainstTheLibrary(unittest.TestCase):

    def setUp(self):
        self.src = scratch(self) / "src"
        write_library(self.src)
        self.sha = hashlib.sha256(declarations_bytes()).hexdigest()

    def check(self, table: dict, declarations: dict | None = None) -> list[str]:
        return itemtable.check_table(table, declarations=declarations or doc.DECLARATIONS,
                                     declarations_sha256=self.sha,
                                     source=itemtable.SourceTree(self.src),
                                     library_commit=doc.COMMIT)

    def test_the_synthetic_table_passes(self):
        self.assertEqual(self.check(numbered_table()), [])

    def test_each_mismatch_is_caught(self):
        good = numbered_table()
        cases = {
            "not in declarations.json": (lambda d: d["items"][0]["declarations"][0].update(
                name="ExampleLib.Nope"), "not in declarations.json"),
            "another module": (lambda d: d["items"][0]["declarations"][0].update(
                module="ExampleLib.Other"), "is in module"),
            "another line": (lambda d: d["items"][0]["declarations"][0].update(line=13),
                             "keyword line is 12"),
            "another commit": (lambda d: d["library"].update(commit="f" * 40),
                               "is not the library commit"),
            "another scope": (lambda d: d["library"].update(declarations_sha256="e" * 64),
                              "another scope"),
            "a reference to no declaration": (lambda d: d["items"][0]["reference"].update(
                declaration="ExampleLib.Nope"), "reference.declaration"),
        }
        for what, (change, words) in cases.items():
            with self.subTest(case=what):
                table = copy.deepcopy(good)
                change(table)
                found = self.check(table)
                self.assertTrue(any(words in p for p in found), found)

    def test_the_keyword_line_must_hold_the_keyword_and_the_name(self):
        rows = copy.deepcopy(doc.DECLARATIONS)
        by = {d["name"]: d for d in rows["declarations"]}
        source = itemtable.SourceTree(self.src)
        self.assertIsNone(itemtable.keyword_problem(by["ExampleLib.cover_mono"], source))  # lemma
        self.assertIsNone(itemtable.keyword_problem(by["ExampleLib.chain_short"], source))
        wrong_kind = dict(by["ExampleLib.GlueData"], kind="class")
        self.assertIn("holds no class", itemtable.keyword_problem(wrong_kind, source))
        other_name = dict(by["ExampleLib.glue_map"], name="ExampleLib.glue_maps")
        self.assertIn("does not name glue_maps", itemtable.keyword_problem(other_name, source))
        # the keyword is a whole word: `theorem` is not in `theorems_about`
        (self.src / SOURCE_PATH).write_text("theorems_about glue_card\n", encoding="utf-8")
        at_one = dict(by["ExampleLib.glue_card"], line=1)
        self.assertIn("holds no theorem", itemtable.keyword_problem(
            at_one, itemtable.SourceTree(self.src)))

    def test_a_name_in_a_comment_is_not_a_keyword_line(self):
        (self.src / "D.lean").write_text(
            "/-- The glue theorem: `glue_theorem` says the square is a pushout. -/\n"
            "theorem glue_theorem : True := trivial -- by glue_theorem\n"
            "theorem other : True := trivial -- glue_theorem\n"
            "  the theorem glue_theorem. -/ theorem glue_theorem : True := trivial\n",
            encoding="utf-8")
        source = itemtable.SourceTree(self.src)
        entry = {"name": "ExampleLib.glue_theorem", "kind": "theorem", "path": "D.lean"}
        self.assertIn("outside a comment", itemtable.keyword_problem(dict(entry, line=1), source))
        self.assertIsNone(itemtable.keyword_problem(dict(entry, line=2), source))
        self.assertIn("does not name", itemtable.keyword_problem(dict(entry, line=3), source))
        self.assertIsNone(itemtable.keyword_problem(dict(entry, line=4), source))

    def test_partial_and_irreducible_defs_are_keyword_lines(self):
        (self.src / "P.lean").write_text("partial def loop (n : Nat) : Nat := loop n\n"
                                         "irreducible_def sealed : Nat := 1\n", encoding="utf-8")
        source = itemtable.SourceTree(self.src)
        self.assertIsNone(itemtable.keyword_problem(
            {"name": "A.loop", "kind": "opaque", "path": "P.lean", "line": 1}, source))
        self.assertIsNone(itemtable.keyword_problem(
            {"name": "A.sealed", "kind": "def", "path": "P.lean", "line": 2}, source))

    def test_only_a_key_declaration_s_line_is_read_in_the_source(self):
        # a helper @[simps] generated carries its generating declaration's line
        rows = copy.deepcopy(doc.DECLARATIONS)
        for row in rows["declarations"]:
            if row["name"] == "ExampleLib.glue_map":
                row.update(kind="theorem", line=40)
        table = numbered_table()
        for item in table["items"]:
            for decl in item["declarations"]:
                if decl["name"] == "ExampleLib.glue_map":
                    self.assertEqual(decl["role"], "helper")
                    decl["line"] = 40
        self.assertEqual(self.check(table, rows), [])
        for item in table["items"]:
            for decl in item["declarations"]:
                if decl["name"] == "ExampleLib.glue_map":
                    decl["role"] = "key"
                    decl["part"] = "a"
        self.assertTrue(any("does not name glue_map" in p for p in self.check(table, rows)))

    def test_an_instance_needs_no_name_on_its_line(self):
        (self.src / "I.lean").write_text("instance : Inhabited Foo := ⟨x⟩\n", encoding="utf-8")
        entry = {"name": "ExampleLib.instInhabitedFoo", "kind": "instance", "path": "I.lean",
                 "line": 1}
        self.assertIsNone(itemtable.keyword_problem(entry, itemtable.SourceTree(self.src)))

    def test_a_source_file_is_never_read_through_a_link_out_of_the_tree(self):
        outside = scratch(self) / "Outside.lean"
        outside.write_text("theorem glue_theorem\n", encoding="utf-8")
        (self.src / "Link.lean").symlink_to(outside)
        entry = {"name": "ExampleLib.glue_theorem", "kind": "theorem", "path": "Link.lean",
                 "line": 1}
        self.assertIn("not in library/src/",
                      itemtable.keyword_problem(entry, itemtable.SourceTree(self.src)))


# ---------------------------------------------------------------------------
# the commands in a workspace

class _Workspace(unittest.TestCase):

    def setUp(self):
        # this suite may run inside an agent's shell (a Claude Code session sets CLAUDECODE),
        # where approve refuses; and no git variable may point at another repository
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in AGENT_ENVIRONMENT + GIT_LOCATION_ENV:
            os.environ.pop(name, None)
        self.root = scratch(self) / "ws"
        code, out, err = cf("init", str(self.root), "--no-git", "-q")
        self.assertEqual(code, 0, out + err)
        self.paths = WorkspacePaths(self.root)
        self.repo = self.paths.informalize_repo(ID)

    def cf(self, *argv: str, code: int = 0) -> str:
        got, out, err = cf("--workspace", str(self.root), *argv)
        self.assertEqual(got, code, out + err)
        return out + err

    def init(self, *extra: str, code: int = 0) -> str:
        return self.cf("informalize", "init", ID, *INIT, *extra, code=code)

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                              text=True, check=True)
        return done.stdout

    def commits(self) -> int:
        return int(self.git("rev-list", "--count", "HEAD").strip())


class TestInit(_Workspace):

    def test_init_writes_the_settings_the_repository_and_the_receipt(self):
        self.init()
        settings = read_yaml(self.paths.informalize_yaml(ID))
        self.assertEqual(config.problems(settings), [])
        self.assertEqual(settings["reference"]["work"], "A. Author, Gluing, Example Press, 2001")
        self.assertIsNone(settings["caps"]["max_cost_per_phase"])
        for relative in ("README.md", "LICENSE", ".gitignore", "informal/main.tex",
                         "informal/notation.sty", "informal/STYLE-project.md",
                         "informal/sections/00-conventions.tex", "informal/fcmarkers.sty",
                         "informal/tools/build_pdf.sh", "informal/informalize.json",
                         "informal/GENERATED.json", "informal/LICENSE-runtime"):
            with self.subTest(file=relative):
                self.assertTrue((self.repo / relative).is_file())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.commits(), 1)
        self.assertEqual(OutputRepo(self.paths, ID).stamped_id(), ID)
        receipt = json.loads(self.paths.informalize_receipt(ID, "I0").read_text(encoding="utf-8"))
        self.assertEqual(receipt["stage"], "I0")
        self.assertEqual(set(receipt["outputs"]), {"repo/informal/informalize.json",
                                                   "repo/informal/GENERATED.json"})
        # nothing of the runtime's bytecode is copied or left behind
        self.assertEqual(list((SKILL / "templates" / "informalize").rglob("__pycache__")), [])
        self.assertEqual(list(self.repo.rglob("*.pyc")), [])

    def test_the_project_files_carry_the_settings(self):
        self.init()
        main = (self.repo / "informal/main.tex").read_text(encoding="utf-8")
        readme = (self.repo / "README.md").read_text(encoding="utf-8")
        style = (self.repo / "informal/STYLE-project.md").read_text(encoding="utf-8")
        for text in (main, readme, style):
            self.assertNotIn("{{", text)
        self.assertIn(r"\texttt{ExampleLib.glue\_theorem}", main)
        self.assertIn(r"\bibitem{reference} A. Author, Gluing, Example Press, 2001.", main)
        self.assertIn("the Apache License, Version~2.0", main)
        self.assertIn(f"[ExampleLib]({URL})", readme)
        self.assertIn("by number only, through `\\fcref`", style)
        self.assertEqual((self.repo / ".gitignore").read_text(encoding="utf-8"),
                         (SKILL / "templates/informalize/project/gitignore").read_text(encoding="utf-8"))
        config_ = json.loads((self.repo / "informal/informalize.json").read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in config_.items() if k != "source_date_epoch"},
                         {k: v for k, v in spec_examples()["formal-companion/informalize-runtime"]
                          .items() if k != "source_date_epoch"})

    def test_without_a_reference_work_the_text_cites_none(self):
        args = INIT[:INIT.index("--reference")]
        self.cf("informalize", "init", ID, *args)
        main = (self.repo / "informal/main.tex").read_text(encoding="utf-8")
        self.assertNotIn(r"\bibitem{reference}", main)
        self.assertIn("uses no `\\fcref`",
                      (self.repo / "informal/STYLE-project.md").read_text(encoding="utf-8"))
        self.assertNotIn("reference", read_yaml(self.paths.informalize_yaml(ID)))

    def test_a_second_init_changes_nothing(self):
        self.init()
        head = self.git("rev-parse", "HEAD")
        out = self.init()
        self.assertIn("nothing to commit", out)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_other_settings_are_refused_unless_forced(self):
        self.init()
        (self.repo / "informal/main.tex").write_text("% mine\n", encoding="utf-8")
        out = self.init("--title", "Another title", code=1)
        self.assertIn("text.title", out)
        self.init("--title", "Another title", "--text-license", "MIT", "--force")
        self.assertEqual(read_yaml(self.paths.informalize_yaml(ID))["text"]["title"], "Another title")
        # the project files are the maintainer's; LICENSE follows the license
        self.assertEqual((self.repo / "informal/main.tex").read_text(encoding="utf-8"), "% mine\n")
        self.assertTrue((self.repo / "LICENSE").read_text(encoding="utf-8").startswith("MIT License"))
        self.assertIn("informal/main.tex", self.git("status", "--porcelain"))

    def test_a_forced_change_of_another_setting_keeps_the_license(self):
        self.init()
        license_path = self.repo / "LICENSE"
        edited = "Copyright 2026 A. Maintainer\n\n" + license_path.read_text(encoding="utf-8")
        license_path.write_text(edited, encoding="utf-8")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "my header")
        head = self.git("rev-parse", "HEAD")
        out = self.init("--workers", "5", "--force")
        self.assertNotIn("LICENSE is now", out)
        self.assertEqual(license_path.read_text(encoding="utf-8"), edited)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(read_yaml(self.paths.informalize_yaml(ID))["caps"]["workers"], 5)

    def test_force_replaces_a_settings_file_that_does_not_parse(self):
        self.init()
        yaml_path = self.paths.informalize_yaml(ID)
        yaml_path.write_text("id: a: b\n  c: d\n", encoding="utf-8")
        self.assertIn("--force", self.cf("informalize", "status", ID, code=2))
        self.init(code=2)
        self.init("--force")
        self.assertEqual(config.problems(read_yaml(yaml_path)), [])

    def test_a_no_op_leaves_the_receipt_as_it_was(self):
        self.init()
        receipt = self.paths.informalize_receipt(ID, "I0")
        before = receipt.read_bytes()
        with mock.patch("informalize.stages.now", return_value="2099-01-01T00:00:00Z"):
            self.init()
            self.cf("informalize", "sync", ID)
        self.assertEqual(receipt.read_bytes(), before)

    def test_a_bad_flag_is_refused_before_anything_is_written(self):
        cases = {"an http URL": ("--library", "http://example.org/x"),
                 "a short commit": ("--commit", "abc"),
                 "a module outside the prefix": ("--prefix", "Other"),
                 "a timeout past 3h": ("--agent-timeout", "5h")}
        for what, (flag, value) in cases.items():
            with self.subTest(case=what):
                args = list(INIT)
                if flag in args:
                    args[args.index(flag) + 1] = value
                else:
                    args += [flag, value]
                got, out, err = cf("--workspace", str(self.root), "informalize", "init", ID, *args)
                self.assertNotEqual(got, 0, out + err)
                self.assertFalse(self.paths.informalize_dir(ID).exists())
        got, _, err = cf("--workspace", str(self.root), "informalize", "init", "Bad_Id", *INIT)
        self.assertNotEqual(got, 0)
        got, _, err = cf("--workspace", str(self.root), "informalize", "init", ID, *INIT[:-2],
                         "--text-license", "GPL-3.0")
        self.assertEqual(got, 2)

    def test_each_open_license_writes_its_text(self):
        for license_ in OPEN_LICENSES:
            with self.subTest(license=license_):
                settings = config.build(iid=ID, url=URL, commit=doc.COMMIT, module="A.B",
                                        theorem="A.c", prefix="A", text_license=license_,
                                        title="T", author="A. Maintainer")
                text = project.license_text(settings, year=2026).decode("utf-8")
                self.assertNotIn("{{", text)
                self.assertTrue(project.license_matches(settings, text.encode("utf-8")))
                other = config.build(iid=ID, url=URL, commit=doc.COMMIT, module="A.B",
                                     theorem="A.c", prefix="A",
                                     text_license="MIT" if license_ != "MIT" else "CC0-1.0",
                                     title="T", author="A. Maintainer")
                self.assertFalse(project.license_matches(other, text.encode("utf-8")))
        mit = project.license_text(config.build(
            iid=ID, url=URL, commit=doc.COMMIT, module="A.B", theorem="A.c", prefix="A",
            text_license="MIT", title="T", author="A. Maintainer"), year=2026).decode("utf-8")
        self.assertIn("Copyright (c) 2026 A. Maintainer", mit)
        self.assertEqual((SKILL / "templates/informalize/licenses/Apache-2.0.txt").read_bytes(),
                         (SKILL / "LICENSE").read_bytes())

    def test_init_refuses_a_repository_it_did_not_make(self):
        self.repo.mkdir(parents=True)
        (self.repo / "file").write_text("x", encoding="utf-8")
        out = self.init(code=2)
        self.assertIn("is not a git repository", out)
        shutil.rmtree(self.repo)
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "informal").mkdir()
        (self.repo / "informal/informalize.json").write_text('{"id": "another"}', encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "x"], check=True)
        out = self.init(code=2)
        self.assertIn("did not make", out)

    def test_init_takes_up_no_repository_holding_other_files(self):
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "informal").mkdir()
        (self.repo / "informal/fcmarkers.sty").write_text("% mine\n", encoding="utf-8")
        out = self.init(code=2)
        self.assertIn("informal/fcmarkers.sty", out)
        self.assertEqual((self.repo / "informal/fcmarkers.sty").read_text(encoding="utf-8"),
                         "% mine\n")
        # what a first init left before its stamp is taken up
        shutil.rmtree(self.repo)
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "README.md").write_bytes(project.project_files(config.build(
            iid=ID, url=URL, commit=doc.COMMIT, module="ExampleLib.Topology.Glue.Basic",
            theorem="ExampleLib.glue_theorem", prefix="ExampleLib", text_license="Apache-2.0",
            title="The glue theorem, after ExampleLib", author="A. Maintainer",
            reference="A. Author, Gluing, Example Press, 2001"))["README.md"])
        self.init()
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_init_refuses_a_git_file_pointing_elsewhere(self):
        other = scratch(self) / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.repo.mkdir(parents=True)
        (self.repo / ".git").write_text(f"gitdir: {other / '.git'}\n", encoding="utf-8")
        self.assertIn("is not a directory", self.init(code=2))
        done = subprocess.run(["git", "-C", str(other), "rev-list", "--all"], capture_output=True,
                              text=True)
        self.assertEqual(done.stdout, "")

    def test_init_refuses_a_symlinked_informalize_directory(self):
        outside = scratch(self) / "outside"
        outside.mkdir()
        self.paths.informalize_root.symlink_to(outside)
        self.assertIn("resolves outside the workspace", self.init(code=2))
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_git_variable_cannot_send_the_commits_elsewhere(self):
        other = scratch(self) / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"),
                                          "GIT_WORK_TREE": str(other)}):
            self.init()
        self.assertEqual(self.commits(), 1)
        done = subprocess.run(["git", "-C", str(other), "rev-list", "--all"], capture_output=True,
                              text=True)
        self.assertEqual(done.stdout, "")

    def test_init_refuses_a_symlinked_repository(self):
        elsewhere = scratch(self) / "elsewhere"
        elsewhere.mkdir()
        self.paths.informalize_dir(ID).mkdir(parents=True)
        self.repo.symlink_to(elsewhere)
        out = self.init(code=2)
        self.assertIn("is a symlink", out)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_an_old_workspace_gets_the_ignore_lines(self):
        gitignore = self.paths.gitignore
        text = gitignore.read_text(encoding="utf-8")
        for line in ("informalize/*/repo/", "informalize/*/library/", "informalize/*/pdf/"):
            text = text.replace(line + "\n", "")
        gitignore.write_text(text, encoding="utf-8")
        self.init()
        after = gitignore.read_text(encoding="utf-8")
        self.assertIn("\ninformalize/*/repo/\n", after)
        self.init()
        self.assertEqual(gitignore.read_text(encoding="utf-8"), after)


class TestTheException(_Workspace):
    """Section 1.3: only the stamped repository, only the listed paths, never through a link."""

    def test_a_write_lands_only_on_the_listed_paths(self):
        self.init()
        repo = OutputRepo(self.paths, ID)
        for good in ("informal/x.tex", "annotations/1/a.json", "pdf-overlay/GENERATED.json",
                     "site/index.html", ".github/workflows/tests.yml",
                     ".lake/overlay-pdf/x.pdf", "README.md", "LICENSE", ".gitignore"):
            with self.subTest(path=good):
                repo.target(good)
        for bad in ("site/other.html", "lean/X.lean", "informal/../x", "/etc/passwd",
                    "informal//x", "Makefile", ".git/config", "informal/./x"):
            with self.subTest(path=bad), self.assertRaises(CfError):
                repo.target(bad)

    def test_no_write_goes_through_a_link(self):
        self.init()
        outside = scratch(self)
        shutil.rmtree(self.repo / "informal/sections")
        (self.repo / "informal/sections").symlink_to(outside)
        with self.assertRaises(CfError):
            OutputRepo(self.paths, ID).write("informal/sections/01-a.tex", b"x")
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_repository_without_the_stamp_is_refused(self):
        self.init()
        (self.repo / "informal/informalize.json").write_text('{"id": "other"}\n', encoding="utf-8")
        out = self.cf("informalize", "sync", ID, code=2)
        self.assertIn("carries no stamp", out)


class TestSync(_Workspace):

    def setUp(self):
        super().setUp()
        self.init()

    def test_sync_check_passes_after_init_and_names_each_drift(self):
        self.cf("informalize", "sync", ID, "--check")
        (self.repo / "informal/fcmarkers.sty").write_text("% edited\n", encoding="utf-8")
        (self.repo / "informal/tools/proof_sheets.py").unlink()
        (self.repo / "informal/tools/mine.py").write_text("x = 1\n", encoding="utf-8")
        out = self.cf("informalize", "sync", ID, "--check", code=1)
        self.assertIn("changed: informal/fcmarkers.sty", out)
        self.assertIn("missing: informal/tools/proof_sheets.py", out)
        self.assertIn("extra: informal/tools/mine.py", out)

    def test_sync_refuses_a_file_of_its_own_directory_it_did_not_write(self):
        (self.repo / "informal/tools/mine.py").write_text("x = 1\n", encoding="utf-8")
        out = self.cf("informalize", "sync", ID, code=2)
        self.assertIn("informal/tools/mine.py", out)
        self.assertTrue((self.repo / "informal/tools/mine.py").exists())

    def test_sync_restores_and_is_then_a_no_op(self):
        (self.repo / "informal/fcmarkers.sty").write_text("% edited\n", encoding="utf-8")
        before = self.commits()
        self.cf("informalize", "sync", ID)
        # the restored file is the committed one again: nothing to commit
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.cf("informalize", "sync", ID, "--check")
        self.cf("informalize", "sync", ID)
        self.assertEqual(self.commits(), before)

    def test_sync_deletes_what_the_templates_no_longer_have(self):
        record = json.loads((self.repo / "informal/GENERATED.json").read_text(encoding="utf-8"))
        record["files"].append({"path": "tools/retired.py", "sha256": "0" * 64})
        (self.repo / "informal/GENERATED.json").write_text(json.dumps(record), encoding="utf-8")
        (self.repo / "informal/tools/retired.py").write_text("x\n", encoding="utf-8")
        # as an older sync left it, committed
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "add", "informal")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "older runtime")
        before = self.commits()
        self.cf("informalize", "sync", ID)
        self.assertFalse((self.repo / "informal/tools/retired.py").exists())
        self.cf("informalize", "sync", ID, "--check")
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_record_cannot_delete_a_project_file(self):
        record = json.loads((self.repo / "informal/GENERATED.json").read_text(encoding="utf-8"))
        record["files"].append({"path": "main.tex", "sha256": "0" * 64})
        (self.repo / "informal/GENERATED.json").write_text(json.dumps(record), encoding="utf-8")
        out = self.cf("informalize", "sync", ID, code=2)
        self.assertIn("informal/main.tex, which is not the runtime's", out)
        self.assertTrue((self.repo / "informal/main.tex").is_file())

    def test_python_s_cache_in_tools_is_ignored(self):
        cache = self.repo / "informal/tools/__pycache__"
        cache.mkdir()
        (cache / "fccommon.cpython-313.pyc").write_bytes(b"\0")
        self.cf("informalize", "sync", ID, "--check")
        self.cf("informalize", "sync", ID)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_the_library_commit_s_time_is_kept_until_the_commit_moves(self):
        path = self.repo / "informal/informalize.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["source_date_epoch"] = 1700000000                    # what I1 writes
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.cf("informalize", "sync", ID, "--check")
        self.cf("informalize", "sync", ID)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["source_date_epoch"],
                         1700000000)
        self.init("--commit", "f" * 40, "--force")
        self.assertIsNone(json.loads(path.read_text(encoding="utf-8"))["source_date_epoch"])

    def test_an_edit_of_the_settings_reaches_informalize_json(self):
        yaml_path = self.paths.informalize_yaml(ID)
        settings = read_yaml(yaml_path)
        settings["library"]["theorem"] = "ExampleLib.glue_surjective"
        config.write(yaml_path, settings)
        out = self.cf("informalize", "sync", ID, "--check", code=1)
        self.assertIn("is not what informalize.yaml gives", out)
        self.cf("informalize", "sync", ID)
        data = json.loads((self.repo / "informal/informalize.json").read_text(encoding="utf-8"))
        self.assertEqual(data["library"]["theorem"], "ExampleLib.glue_surjective")

    def test_a_broken_settings_file_is_refused(self):
        yaml_path = self.paths.informalize_yaml(ID)
        yaml_path.write_text(yaml_path.read_text(encoding="utf-8").replace(
            "license: Apache-2.0", "license: Proprietary"), encoding="utf-8")
        out = self.cf("informalize", "sync", ID, code=2)
        self.assertIn("text.license", out)


class _Table(_Workspace):
    """A workspace past I0, with a fake I1 (library/src) and I2 (declarations.json)."""

    def setUp(self):
        super().setUp()
        self.init()
        write_library(self.paths.informalize_library_src(ID))
        # what I2 writes and commits
        (self.repo / "informal/declarations.json").write_bytes(declarations_bytes())
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "add", "informal/declarations.json")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "scope")
        self.write_items(provisional_table())

    def write_items(self, table: dict) -> None:
        (self.repo / "informal/items.json").write_text(
            json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    def items(self) -> dict:
        return json.loads((self.repo / "informal/items.json").read_text(encoding="utf-8"))

    def approve_items(self, *extra: str, code: int = 0) -> str:
        return self.cf("informalize", "approve", ID, "items", "--by", "A. Maintainer", *extra,
                       code=code)


class TestCheckItems(_Table):

    def test_check_items_passes_a_good_table_and_names_a_bad_one(self):
        out = self.cf("informalize", "check-items", ID)
        self.assertIn("7 items in 3 sections", out)
        table = provisional_table()
        table["items"][0]["declarations"][0]["line"] = 13
        self.write_items(table)
        out = self.cf("informalize", "check-items", ID, code=1)
        self.assertIn("keyword line is 12", out)

    def test_check_items_names_the_stage_that_is_missing(self):
        shutil.rmtree(self.paths.informalize_library(ID))
        self.assertIn("stage I1 has not run", self.cf("informalize", "check-items", ID, code=2))
        write_library(self.paths.informalize_library_src(ID))
        (self.repo / "informal/declarations.json").unlink()
        self.assertIn("stage I2 has not run", self.cf("informalize", "check-items", ID, code=2))


class TestApproveItems(_Table):

    def test_the_first_approval_renumbers_and_writes_the_sections(self):
        before = self.commits()
        out = self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        self.assertIn("pins repo/informal/items.json", out)
        table = self.items()
        self.assertEqual([it["id"] for it in table["items"]], ["1.1", "1.2", "2.1", "2.2", "2.3"])
        self.assertEqual([it["id"] for it in table["deferred"]], ["2.9", "3.1"])
        self.assertEqual(table["decisions"][-1]["approved_by"], "A. Maintainer")
        self.assertEqual((self.repo / "informal/sections.tex").read_text(encoding="utf-8"),
                         doc.SECTIONS_TEX)
        for name in ("01-gluing-spaces.tex", "02-covers.tex"):
            self.assertEqual((self.repo / "informal/sections" / name).read_bytes(), b"")
        self.assertFalse((self.repo / "informal/sections/03-later.tex").exists())
        self.assertEqual(self.commits(), before + 1)
        approval = json.loads(self.paths.informalize_approval(ID, "items").read_text(encoding="utf-8"))
        self.assertEqual(approval["pins"]["repo/informal/items.json"],
                         hashlib.sha256((self.repo / "informal/items.json").read_bytes()).hexdigest())
        self.assertFalse(approval["frozen"])
        self.cf("informalize", "check-items", ID)

    def test_in_v1_is_required_and_checked_before_the_first_draft(self):
        self.assertIn("--in-v1 is required", self.approve_items(code=2))
        self.assertIn("does not have", self.approve_items("--in-v1", "1.1,9.9", code=2))
        table = provisional_table()
        table["items"][0]["kind"] = "axiom"
        self.write_items(table)
        self.assertIn("problem(s)", self.approve_items("--in-v1", "all", code=1))
        self.assertFalse(self.paths.informalize_approval(ID, "items").exists())

    def test_an_item_left_out_needs_no_key(self):
        table = provisional_table()
        table["items"][-1]["declarations"] = []          # 3.1, drafted in v1, keyless
        self.write_items(table)
        self.assertIn("no key declaration", self.cf("informalize", "check-items", ID, code=1))
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        self.assertEqual(self.items()["deferred"][-1]["id"], "3.1")

    def test_an_approval_before_the_draft_may_renumber_again(self):
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7,3.1")
        self.assertEqual([s["number"] for s in self.items()["sections"]], ["1", "2", "3"])
        self.assertTrue((self.repo / "informal/sections/03-later.tex").is_file())
        self.approve_items("--in-v1", "1.1,1.2,2.1,2.2,2.3")
        self.assertFalse((self.repo / "informal/sections/03-later.tex").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_once_a_section_is_drafted_the_ids_are_frozen(self):
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        (self.repo / "informal/sections/01-gluing-spaces.tex").write_text(doc.SECTION_1,
                                                                          encoding="utf-8")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "draft")
        out = self.approve_items()                             # pins, renumbers nothing
        self.assertIn("frozen", out)
        table = self.items()
        # a withdrawal without a record is refused, and so is reusing the gap
        self.write_items(dict(table, items=[it for it in table["items"] if it["id"] != "2.2"]))
        self.assertIn("without gaps", self.approve_items(code=1))
        withdrawn = dict(table, items=[it for it in table["items"] if it["id"] != "2.2"],
                         decisions=table["decisions"] + [{"withdrawn": "2.2", "why": "merged"}])
        self.write_items(withdrawn)
        self.approve_items()
        changed = copy.deepcopy(withdrawn)
        changed["items"][0]["kind"] = "lemma"
        self.write_items(changed)
        self.assertIn("kind is frozen", self.approve_items(code=1))
        # an item at the end of its section is fine, and --in-v1 only flips flags
        added = copy.deepcopy(withdrawn)
        added["items"].append(dict(copy.deepcopy(added["items"][-1]), id="2.4"))
        self.write_items(added)
        self.approve_items("--in-v1", "1.1,1.2,2.1,2.3")
        flags = {it["id"]: it["in_v1"] for it in self.items()["items"]}
        self.assertEqual(flags, {"1.1": True, "1.2": True, "2.1": True, "2.3": True, "2.4": False})
        self.assertEqual((self.repo / "informal/sections/01-gluing-spaces.tex")
                         .read_text(encoding="utf-8"), doc.SECTION_1)

    def test_an_agent_cannot_approve(self):
        for name, value in (("CF_ROLE", "worker"), ("TEND_TASK_ID", "items"),
                            ("CLAUDECODE", "1"), ("CLAUDE_CODE_ENTRYPOINT", "sdk-py")):
            with self.subTest(variable=name), mock.patch.dict(os.environ, {name: value}):
                self.assertIn("maintainer's", self.approve_items("--in-v1", "all", code=2))
        self.assertFalse(self.paths.informalize_approval(ID, "items").exists())

    def approval(self) -> dict:
        return json.loads(self.paths.informalize_approval(ID, "items").read_text(encoding="utf-8"))

    def assert_commit_holds_the_pin(self) -> None:
        approval = self.approval()
        self.assertEqual(self.git("status", "--porcelain"), "")
        held = subprocess.run(["git", "-C", str(self.repo), "show",
                               f"{approval['commit']}:informal/items.json"],
                              capture_output=True, check=True).stdout
        self.assertEqual(hashlib.sha256(held).hexdigest(),
                         approval["pins"]["repo/informal/items.json"])

    def test_a_table_fixed_by_hand_in_the_bytes_approve_writes_is_committed(self):
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        table = self.items()
        table["items"][0]["gist"] = "Another gist."              # before the first draft
        (self.repo / "informal/items.json").write_bytes(itemtable.dumps(table))
        self.approve_items()
        self.assert_commit_holds_the_pin()
        (self.repo / "informal/sections/01-gluing-spaces.tex").write_text(doc.SECTION_1,
                                                                          encoding="utf-8")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "draft")
        table = self.items()
        table["items"][1]["reference"]["number"] = "Theorem 4.2"   # after it
        (self.repo / "informal/items.json").write_bytes(itemtable.dumps(table))
        self.assertIn("frozen", self.approve_items())
        self.assert_commit_holds_the_pin()

    def test_the_ids_stay_frozen_when_the_drafted_section_is_emptied(self):
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        section = self.repo / "informal/sections/01-gluing-spaces.tex"
        section.write_text(doc.SECTION_1, encoding="utf-8")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "draft")
        self.approve_items()
        section.write_text("", encoding="utf-8")                   # a reverted draft
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "revert")
        self.approve_items("--in-v1", "1.2,2.1,2.2,2.3")
        self.assertEqual([it["id"] for it in self.items()["items"]],
                         ["1.1", "1.2", "2.1", "2.2", "2.3"])
        self.assertFalse(self.items()["items"][0]["in_v1"])
        self.assertTrue(self.approval()["frozen"])

    def test_a_withdrawn_id_is_never_taken_back(self):
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        (self.repo / "informal/sections/01-gluing-spaces.tex").write_text(doc.SECTION_1,
                                                                          encoding="utf-8")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "draft")
        table = self.items()
        last = table["items"][-1]
        self.assertEqual(last["id"], "2.3")
        table["items"] = table["items"][:-1]
        table["decisions"] = table["decisions"] + [{"withdrawn": "2.3", "why": "merged"}]
        self.write_items(table)
        self.approve_items()
        self.assertEqual(self.approval()["snapshot"]["withdrawn"], ["2.3"])
        table["decisions"] = table["decisions"][:-1]
        table["items"].append(dict(last, kind="remark", title="Another item"))
        self.write_items(table)
        self.assertIn("never reused", self.approve_items(code=1))

    def test_a_deferred_item_names_the_numbering_it_came_from(self):
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        deferred = self.items()["deferred"]
        at = self.items()["decisions"][-1]["at"]
        self.assertEqual({it["x_deferred_at"] for it in deferred}, {at})
        table = self.items()
        table["deferred"][0]["kind"] = "axiom"
        self.write_items(table)
        self.assertIn("deferred item 2.9", self.cf("informalize", "check-items", ID, code=1))


class TestReferences(_Table):

    def test_the_checklist_and_its_approval_follow_the_approved_table(self):
        out = self.cf("informalize", "reference-checklist", ID, code=2)
        self.assertIn("approve", out)
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        self.assertIn("does not show", self.cf("informalize", "approve", ID, "references",
                                               "--by", "A. Maintainer", code=2))
        self.cf("informalize", "reference-checklist", ID)
        text = self.paths.informalize_reference_checklist(ID).read_text(encoding="utf-8")
        self.assertIn("| [ ] | 1.2 | theorem | The glue theorem | Theorem 4.1 | inferred |  |", text)
        self.assertIn("Reference work: A. Author, Gluing, Example Press, 2001.", text)
        # ticking a box does not void it, and writing the list again keeps the tick
        self.paths.informalize_reference_checklist(ID).write_text(
            text.replace("| [ ] | 1.1", "| [x] | 1.1"), encoding="utf-8")
        self.cf("informalize", "reference-checklist", ID)
        self.assertIn("| [x] | 1.1 |", self.paths.informalize_reference_checklist(ID)
                      .read_text(encoding="utf-8"))
        self.cf("informalize", "approve", ID, "references", "--by", "A. Maintainer")
        status = self.cf("informalize", "status", ID)
        self.assertRegex(status, r"approve references\s+: current")
        # a number fixed by hand voids both approvals until they are given again
        table = self.items()
        table["items"][1]["reference"]["number"] = "Theorem 4.2"
        self.write_items(table)
        status = self.cf("informalize", "status", ID)
        self.assertRegex(status, r"approve items\s+: stale")
        self.assertRegex(status, r"approve references\s+: stale")
        self.approve_items()
        self.cf("informalize", "reference-checklist", ID)
        self.cf("informalize", "approve", ID, "references", "--by", "A. Maintainer")
        self.assertRegex(self.cf("informalize", "status", ID), r"approve references\s+: current")

    def test_a_change_elsewhere_in_the_table_leaves_the_references_approved(self):
        self.approve_items("--in-v1", "1.1,1.4,2.2,2.5,2.7")
        self.cf("informalize", "reference-checklist", ID)
        self.cf("informalize", "approve", ID, "references", "--by", "A. Maintainer")
        table = self.items()
        table["items"][0]["gist"] = "Another gist."
        self.write_items(table)
        status = self.cf("informalize", "status", ID)
        self.assertRegex(status, r"approve items\s+: stale")
        self.assertRegex(status, r"approve references\s+: current")

    def test_the_later_gates_wait_for_their_stages(self):
        out = self.cf("informalize", "approve", ID, "proof-sheets", "--by", "A. Maintainer", code=2)
        self.assertIn("stage I6 has not run", out)
        out = self.cf("informalize", "approve", ID, "publish", "--by", "A. Maintainer", code=2)
        self.assertIn("stage I7 has not run", out)
        out = self.cf("informalize", "approve", ID, "references", "--by", "A. Maintainer",
                      "--in-v1", "all", code=2)
        self.assertIn("items gate only", out)


class TestStatus(_Table):

    def test_status_reads_every_stage_and_names_the_next_step(self):
        out = self.cf("informalize", "status", ID)
        self.assertRegex(out, r"runtime\s+: current")
        self.assertRegex(out, r"LICENSE\s+: the text of Apache-2.0")
        self.assertIn("7 items (6 in version 1) in 3 sections", out)
        self.assertIn("check-items: ok", out)
        self.assertIn(f"cf informalize approve {ID} items --by <name>", out)
        self.assertRegex(out, r"repository\s+: .* 1 uncommitted change")


# ---------------------------------------------------------------------------
# the rendered repository, built (TeX)

class TestTheRenderedRepositoryBuilds(_Table):
    """What `init` renders and `approve items` writes, with the synthetic sections, builds
    with the runtime and passes the checks that need no poppler."""

    def setUp(self):
        missing = [p for p in ("pdflatex", "latexmk", "kpsewhich") if shutil.which(p) is None]
        if not missing:
            missing = [name for name in runtime.TEX_PACKAGES
                       if subprocess.run(["kpsewhich", name], capture_output=True).returncode]
        if missing:
            why = "TeX is missing: " + ", ".join(missing)
            if os.environ.get("CF_REQUIRE_TEX") == "1":
                self.fail(f"{why}, and CF_REQUIRE_TEX=1 requires them")
            raise unittest.SkipTest(why)
        super().setUp()

    def test_it_builds_and_checks(self):
        self.write_items(numbered_table())
        self.approve_items("--in-v1", "1.1,1.2,2.1,2.2,2.3")
        informal = self.repo / "informal"
        (informal / "sections/01-gluing-spaces.tex").write_text(doc.SECTION_1, encoding="utf-8")
        (informal / "sections/02-covers.tex").write_text(doc.SECTION_2, encoding="utf-8")
        (informal / "hypotheses.json").write_text(json.dumps(doc.HYPOTHESES), encoding="utf-8")
        notation = informal / "notation.sty"
        marker = "% Standing hypotheses (one \\fcdefhyp per key of hypotheses.json).\n"
        notation.write_text(notation.read_text(encoding="utf-8").replace(
            marker, marker + doc.NOTATION_HYPOTHESES), encoding="utf-8")
        config_path = informal / "informalize.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        data["source_date_epoch"] = 1700000000                    # what I1 writes
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.cf("informalize", "sync", ID, "--check")
        outdir = scratch(self) / "build"
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
        done = subprocess.run(["bash", "informal/tools/build_pdf.sh", str(outdir)], cwd=self.repo,
                              capture_output=True, text=True, env=env, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        done = subprocess.run([sys.executable, "informal/tools/check_items.py", str(outdir),
                               "--aux-only", "--strict"], cwd=self.repo, capture_output=True,
                              text=True, env=env, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(list(self.repo.rglob("__pycache__")), [])


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
