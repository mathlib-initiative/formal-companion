"""docs/proposals/informalize.md, held to the code it names and to its own examples.

New code, for docs/proposals/informalize.md section 11, component 1. The
informalize mode is specified before it is built, so nothing else would notice
when the specification stops describing this repository. Three kinds of check:

* the code it builds on is there under the names it uses: a renamed function or
  constant would otherwise make the specification wrong without a sound;
* its examples are documents the later components can test against: every JSON
  example parses and obeys the rules section 5 states (and a broken copy is
  refused), and the YAML example is in the subset `cflib.yamlio`'s fallback
  reader reads, with the same result PyYAML gives;
* what it promises is one closed list each -- the `cf informalize`
  subcommands, the fleet phases, the approval gates -- and once a component
  lands, the code agrees with the list.  Those checks skip until then.

It also keeps the mode generic: the first target's theorem is named only in the
acceptance section, and in no file under scripts/, templates/ or tests/ but this one,
which reads its names from that section rather than spelling them.
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
import ast
import copy
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
from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from cflib import vocab                                       # noqa: E402
from cflib.yamlio import load_yaml                            # noqa: E402
from formal_companion_annotations import ids                  # noqa: E402
from formal_companion_annotations import vocab as fmt         # noqa: E402
from overlay import config as overlay_config                  # noqa: E402
from overlay import shards                                    # noqa: E402

SPEC_PATH = SKILL / "docs" / "proposals" / "informalize.md"
SPEC = SPEC_PATH.read_text(encoding="utf-8")
#: the specification with every whitespace run as one space, for phrases a reflow may wrap
FLAT = " ".join(SPEC.split())


# ---------------------------------------------------------------------------
# what the specification promises, as closed lists
# ---------------------------------------------------------------------------

#: Every `cf informalize` subcommand the specification names (sections 3 and 11), with
#: the component of section 11 that builds it: each component is one pull request, so a
#: CLI holds all of a component's subcommands or none of them.
SUBCOMMANDS = {
    "init": "3", "sync": "3", "status": "3", "check-items": "3", "approve": "3",
    "reference-checklist": "3",
    "build-command": "4", "fetch-library": "4", "scope": "4",
    "apply": "5b",
    "pdf-command": "6", "fetch-pdf": "6",
    "overlay": "7", "check-site": "7",
    "verso-command": "9",
}
#: The fleet phases (section 8.2), in the order they run.
PHASES = ("items", "conventions", "draft", "attack", "judge", "edit")
#: What the maintainer signs with `cf informalize approve` (section 13).
APPROVAL_GATES = ("items", "references", "proof-sheets", "publish")
#: Section 5.4's enums.
KINDS = ("definition", "lemma", "proposition", "theorem", "corollary", "example", "remark")
ROLES = ("key", "helper")
RELATIONS = ("exactMatch", "broadMatch", "narrowMatch", "relatedMatch")
REFERENCE_SOURCES = ("docstring", "module docstring", "inferred", "none")
#: Section 8.2's documents.
SEVERITIES = ("wrong", "imprecise", "style")
RULINGS = ("real", "not_real")
ACTIONS = ("fixed", "out_of_scope", "none")
DIRECTIONS = ("lean-first", "tex-first")


# ---------------------------------------------------------------------------
# reading the specification
# ---------------------------------------------------------------------------

def section(number: str) -> str:
    """The text of `## <number>.` (or `### <number>`) up to the next heading of its level."""
    level = "###" if "." in number else "##"
    match = re.search(rf"^{level} {re.escape(number)}\.?\s.*$", SPEC, re.M)
    if match is None:
        raise AssertionError(f"the specification has no section {number}")
    rest = SPEC[match.end():]
    end = re.search(rf"^#{{2,{len(level)}}} ", rest, re.M)
    return rest[:end.start()] if end else rest


def fences(language: str) -> list[str]:
    """The bodies of the fenced blocks of one language, in order."""
    return re.findall(rf"^```{language}\n(.*?)^```", SPEC, re.M | re.S)


def examples() -> dict[str, dict]:
    """``{schema: document}`` for every JSON example."""
    out = {}
    for body in fences("json"):
        doc = json.loads(body)
        out[doc["schema"]] = doc
    return out


def cli_tree() -> dict[str, argparse.ArgumentParser]:
    """``{command: parser}`` of this checkout, from the live parsers."""
    parser = argparse.ArgumentParser(prog="cf")
    subparsers = parser.add_subparsers(dest="command")
    cf_main.discover_commands(subparsers)
    return dict(subparsers.choices)


def subcommands_of(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:                              # noqa: SLF001 - no public API
        if isinstance(action, argparse._SubParsersAction):      # noqa: SLF001
            return set(action.choices)
    return set()


def defined(path: Path, dotted: str) -> bool:
    """Is ``dotted`` (a name, or ``Class.method``) defined at the top of ``path``?"""
    body = ast.parse(path.read_text(encoding="utf-8")).body
    for part in dotted.split("."):
        found = None
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == part:
                    found = node
            elif isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == part for t in node.targets):
                    found = node
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == part:
                    found = node
        if found is None:
            return False
        body = getattr(found, "body", [])
    return True


# ---------------------------------------------------------------------------
# the rules section 5 states, as functions the examples must pass
# ---------------------------------------------------------------------------

def items_problems(doc: dict) -> list[str]:
    """Every rule of section 5.4 an items.json breaks (empty when it keeps them all)."""
    out: list[str] = []
    numbers = [s["number"] for s in doc["sections"]]
    if numbers != [str(i) for i in range(1, len(numbers) + 1)]:
        out.append(f"sections are not numbered 1.. without gaps: {numbers}")
    withdrawn = {d["withdrawn"] for d in doc["decisions"] if isinstance(d, dict) and "withdrawn" in d}
    last: dict[str, int] = {}
    for item in doc["items"]:
        iid = item["id"]
        match = re.fullmatch(r"(\d+)\.(\d+)", iid)
        if match is None:
            out.append(f"{iid}: an id is <section>.<n>")
            continue
        sec, n = match.group(1), int(match.group(2))
        if sec != item["section"] or sec not in numbers:
            out.append(f"{iid}: its section is {item['section']!r}")
        # after the freeze a withdrawn id stays a gap, recorded in decisions
        expected = last.get(sec, 0) + 1
        while f"{sec}.{expected}" in withdrawn:
            expected += 1
        if n != expected:
            out.append(f"{iid}: ids run from 1 without gaps within a section")
        last[sec] = n
        if item["kind"] not in KINDS:
            out.append(f"{iid}: kind {item['kind']!r}")
        letters = [p["letter"] for p in item["parts"]]
        if letters != [chr(ord("a") + i) for i in range(len(letters))]:
            out.append(f"{iid}: parts are lettered a, b, ... in order, not {letters}")
        keys = [d for d in item["declarations"] if d["role"] == "key"]
        if not keys:
            out.append(f"{iid}: no key declaration")
        for decl in item["declarations"]:
            where = f"{iid} {decl['name']}"
            if decl["role"] not in ROLES:
                out.append(f"{where}: role {decl['role']!r}")
            if decl["relation"] not in RELATIONS:
                out.append(f"{where}: relation {decl['relation']!r}")
            if decl["part"] is not None and decl["part"] not in letters:
                out.append(f"{where}: part {decl['part']!r} is not a part of the item")
            if letters and decl["role"] == "key" and decl["part"] is None:
                out.append(f"{where}: a key declaration of an item with parts names its part")
            if decl["role"] == "key" and decl["relation"] != "exactMatch" and not item["departures"]:
                out.append(f"{where}: a key that is not exactMatch says why in departures")
        for letter in letters:
            if not any(d["part"] == letter for d in keys):
                out.append(f"{iid}({letter}): no key declaration")
        references = [item.get("reference")] + [p.get("reference") for p in item["parts"]]
        for ref in filter(None, references):
            if ref["source"] not in REFERENCE_SOURCES or not ref["number"]:
                out.append(f"{iid}: reference {ref}")
    return out


def markers_problems(doc: dict) -> list[str]:
    """Every way a markers.json breaks section 5.6."""
    out: list[str] = []
    entries = {e["id"]: e for e in doc["entries"]}
    for entry in doc["entries"]:
        if "parent" in entry:
            parent = entries.get(entry["parent"])
            if parent is None:
                out.append(f"{entry['id']}: no parent entry {entry['parent']}")
            elif entry["id"] not in parent["parts"]:
                out.append(f"{entry['id']}: its parent does not list it")
            if entry["id"] != f"{entry['parent']}({entry['part']})" or entry["number"] != entry["id"]:
                out.append(f"{entry['id']}: a part is <id>(<letter>), numbered as it is named")
        elif entry["number"] != entry["id"]:
            out.append(f"{entry['id']}: the printed number is the id")
        for box in entry["boxes"]:
            page, x, y, w, h = box
            if not (isinstance(page, int) and 1 <= page <= doc["pages"]):
                out.append(f"{entry['id']}: page {page}")
            if not all(0 <= v <= 1 for v in (x, y, w, h)) or x + w > 1 or y + h > 1:
                out.append(f"{entry['id']}: box {box} is not a fraction of the page")
    return out


# ---------------------------------------------------------------------------
# the code it builds on
# ---------------------------------------------------------------------------

#: (file, symbol defined there, how the specification spells it).
NAMED = (
    ("scripts/overlay/config.py", "OPEN_LICENSES", "overlay.config.OPEN_LICENSES"),
    ("scripts/overlay/export.py", "CONVENTIONS", "overlay.export.CONVENTIONS"),
    ("scripts/overlay/export.py", "export", "overlay.export.export"),
    ("scripts/overlay/sync.py", "init", "overlay.sync.init"),
    ("scripts/overlay/sync.py", "sync", "overlay.sync.sync"),
    ("scripts/overlay/sync.py", "check", "overlay.sync.check"),
    ("scripts/overlay/shards.py", "section_of", "shards.section_of"),
    ("scripts/plan/make_tend_root.py", "make_tend_root", "plan.make_tend_root"),
    ("scripts/gate/verify_findings.py", "changed_entries", "verify_findings.changed_entries"),
    ("scripts/gate/verify_findings.py", "task_from_orchestrator",
     "verify_findings.task_from_orchestrator"),
    ("scripts/informalize/plan.py", "wrapper_env", "informalize.plan.wrapper_env"),
    ("scripts/informalize/runtime.py", "fcitems", "informalize.runtime.fcitems"),
    ("scripts/run/manifest.py", "check_budget_cap", "manifest.check_budget_cap"),
    ("scripts/locate/tend_root.py", "wrapper_env", "tend_root.wrapper_env"),
    ("scripts/locate/tend_root.py", "LOCATE_BIN_HELPERS", "tend_root.LOCATE_BIN_HELPERS"),
    ("scripts/cflib/vocab.py", "decide_proof_status", "cflib.vocab.decide_proof_status"),
    ("scripts/cflib/vocab.py", "RUN_TOKEN_ID", "vocab.RUN_TOKEN_ID"),
    ("scripts/annotations/export/common.py", "pinned_checkout", "common.pinned_checkout"),
    ("scripts/pipeline/stage_receipt.py", "sum_fleets", "pipeline.stage_receipt.sum_fleets"),
    ("scripts/cflib/workspace.py", "Workspace.check_host", "Workspace.check_host"),
    ("scripts/corpus/universe_host.py", "OPTION_DEFAULTS", "universe_host.OPTION_DEFAULTS"),
    ("scripts/corpus/ingest.py", "CANARY", "corpus.ingest.CANARY"),
    ("scripts/corpus/ingest.py", "check_index", "corpus.ingest.check_index"),
    ("scripts/run/manifest.py", "READONLY_CHMOD", "manifest.READONLY_CHMOD"),
    ("scripts/cflib/gitsafe.py", "run", "cflib.gitsafe"),
    ("vendor/formal_companion_annotations/ids.py", "item_label_id", "ids.item_label_id"),
)
#: Modules it names as modules.
MODULES = (("scripts/adapters/leansnippets.py", "adapters.leansnippets"),
           ("scripts/cflib/paths.py", "cflib.paths"))


class TestTheCodeItBuildsOnIsThere(unittest.TestCase):
    """A rename in the code would leave the specification naming nothing."""

    def test_every_symbol_it_names_is_defined_where_it_says(self):
        for rel, symbol, spelled in NAMED:
            with self.subTest(symbol=spelled):
                self.assertIn(spelled, SPEC, f"the specification no longer names {spelled}; "
                                             f"drop the row from NAMED")
                self.assertTrue(defined(SKILL / rel, symbol), f"{rel} defines no {symbol}")

    def test_every_module_it_names_exists(self):
        for rel, spelled in MODULES:
            with self.subTest(module=spelled):
                self.assertIn(spelled, SPEC)
                self.assertTrue((SKILL / rel).is_file(), rel)

    def test_the_tend_root_builder_takes_what_the_planner_passes(self):
        from plan.make_tend_root import make_tend_root
        import inspect
        params = inspect.signature(make_tend_root).parameters
        for name in ("config_template", "gate_files", "extra_env", "agent_timeout", "model"):
            with self.subTest(parameter=name):
                self.assertIn(name, params)

    def test_the_run_stages_it_uses_are_the_run_stages(self):
        from run.remote import STAGE_ORDER
        self.assertEqual(STAGE_ORDER, ["preflight", "transfer", "verify", "smoke", "raise",
                                       "monitor", "stop"])
        self.assertIn("(preflight, transfer, verify, smoke, raise, monitor, stop)", FLAT)

    def test_the_tend_config_it_copies_has_the_settings_it_names(self):
        text = (SKILL / "templates" / "tend" / "config-locate.yaml.tmpl").read_text(encoding="utf-8")
        for setting in ("merge_validation_worktree: true", "seed_worktree_build: false",
                        "batched_merge: true", "--pre-merge", "--changed-files"):
            with self.subTest(setting=setting):
                self.assertIn(setting, text)
                self.assertIn(setting.split(":")[0], SPEC)


class TestTheDriversItRuns(unittest.TestCase):
    """Section 6.1 builds on locate's host drivers as they are."""

    def test_the_universe_line_takes_the_options_it_writes(self):
        from corpus.universe_host import OPTION_DEFAULTS
        for option in ("BUILD_TARGETS", "MODULE_PREFIXES"):
            with self.subTest(option=option):
                self.assertIn(option, OPTION_DEFAULTS)
        # LIBRARY_CACHE arrived with component 4, and keeps today's default.
        self.assertEqual(OPTION_DEFAULTS["LIBRARY_CACHE"], "1")

    def test_the_drivers_it_ships_are_the_ones_probe_command_copies(self):
        from cfcmd.corpus import DRIVERS
        text = section("6.1")
        for path in DRIVERS:
            with self.subTest(driver=path):
                self.assertIn(f"`{Path(path).name}`", text)

    def test_the_drivers_default_floor_is_the_one_it_names(self):
        text = (SKILL / "scripts" / "corpus" / "build_universe_host.sh").read_text(encoding="utf-8")
        floor = re.search(r"^MIN_FREE_GB=\$\{MIN_FREE_GB:-(\d+)\}", text, re.M)
        self.assertIsNotNone(floor)
        self.assertIn(f"default floor ({floor.group(1)} GB)", FLAT)

    def test_dump_deps_takes_the_flags_it_passes(self):
        text = (SKILL / "scripts" / "detect" / "dump_deps.sh").read_text(encoding="utf-8")
        for flag in ("--repo", "--out", "--root-module", "--module-prefix", "--package"):
            with self.subTest(flag=flag):
                self.assertTrue(re.search(rf"^\s*{re.escape(flag)}\)", text, re.M),
                                f"dump_deps.sh takes no {flag}")

    def test_the_probes_stated_range_is_the_one_it_quotes(self):
        text = (SKILL / "templates" / "corpus" / "probe.lean.tmpl").read_text(encoding="utf-8")
        self.assertIn("through v4.36.0-rc1", text)
        self.assertIn("portability through v4.36.0-rc1", FLAT)


class TestTheOverlayFactsItUses(unittest.TestCase):
    """Section 10 rests on these facts about the overlay and the legacy format as they are."""

    def test_the_text_licenses_are_the_ones_pdf_publish_admits(self):
        named = [t for t in re.findall(r"`([^`]+)`", section("1.1"))
                 if re.fullmatch(r"[A-Z][A-Za-z0-9.-]*", t)]
        self.assertEqual(sorted(named), sorted(overlay_config.OPEN_LICENSES))

    def test_an_annotation_id_is_the_kind_and_the_printed_number(self):
        self.assertEqual(ids.item_label_id("theorem", "1.2", "a"), "theorem-1.2-a")
        self.assertIn("`<kind>-<id>[-<letter>]`", SPEC)

    def test_a_destination_href_carries_no_fragment(self):
        """Why the destination's line goes in its own field."""
        self.assertIsNone(shards._HTTPS.fullmatch("https://example.org/a/b#L12"))   # noqa: SLF001
        self.assertIsNotNone(shards._HTTPS.fullmatch("https://example.org/a/b"))   # noqa: SLF001
        self.assertIn("and the fragment `L<line>`", FLAT)

    def test_the_provenance_method_is_one_the_format_admits(self):
        """The format's method list is closed: an `informalize` method fails the checker."""
        methods = re.findall(r"method: ([a-z_]+)", section("10"))
        self.assertEqual(methods, ["agent_annotation"])
        self.assertIn(methods[0], fmt.ANNOTATION_PROVENANCE_METHODS)

    def test_every_relation_it_writes_converts_back(self):
        """Each relation of section 5.4 is written as the legacy token the overlay turns back into it."""
        pairs = dict(re.findall(r"`([a-z]+Match)` → `([a-z_]+)`", " ".join(section("10").split())))
        self.assertEqual(set(pairs), set(RELATIONS))
        for relation, token in pairs.items():
            with self.subTest(relation=relation):
                self.assertIn(token, fmt.ANNOTATION_LINK_RELATIONS)
                self.assertEqual(shards.RELATIONS[token], relation)

    def test_what_it_says_of_the_section_folder_is_true(self):
        folder = shards.section_of({"number": "1.2", "section": "1"}, {})
        self.assertIn(folder, ("other", "1"))
        says_other = "which today goes to `other/`" in FLAT
        self.assertEqual(says_other, folder == "other",
                         "component 7 changed shards.section_of: update section 10")


class TestTheExamples(unittest.TestCase):
    """Section 5's examples are the contract component 3 validates against."""

    def setUp(self):
        self.docs = examples()

    def test_every_json_example_parses_and_names_its_schema(self):
        want = {"formal-companion/informalize-runtime", "formal-companion/informalize-items",
                "formal-companion/informalize-hypotheses", "formal-companion/informalize-markers",
                "formal-companion/informalize-attack", "formal-companion/informalize-ruling"}
        self.assertEqual(set(self.docs), want)
        for doc in self.docs.values():
            self.assertEqual(doc["schema_version"], 1)

    def test_the_yaml_example_is_in_the_subset_the_fallback_reader_reads(self):
        (body,) = fences("yaml")
        minimal = load_yaml(body, prefer_pyyaml=False)
        try:
            import yaml
        except ImportError:                                      # pragma: no cover
            yaml = None
        if yaml is not None:
            self.assertEqual(minimal, yaml.safe_load(body))
        self.assertEqual(minimal["schema"], "formal-companion/informalize")
        self.assertRegex(minimal["library"]["commit"], r"\A[0-9a-f]{40}\Z")
        self.assertTrue(minimal["library"]["url"].startswith("https://"))
        self.assertIsNone(minimal["library"]["license"])
        self.assertIs(minimal["library"]["library_cache"], False)
        self.assertIn(minimal["text"]["license"], overlay_config.OPEN_LICENSES)
        # workspace.yaml is the only place a host or a model is named
        self.assertNotIn("host", minimal)
        caps = minimal["caps"]
        self.assertNotIn("model", caps)
        self.assertLessEqual(caps["workers"] + caps["reviewers"], 16)
        self.assertEqual(minimal["id"], minimal["text"]["job"])
        self.assertRegex(minimal["id"], r"\A[a-z0-9][a-z0-9-]{0,39}\Z")
        self.assertRegex(minimal["id"] + "-lib", r"\A[a-z0-9][a-z0-9._-]{0,63}\Z")
        self.assertTrue(vocab.RUN_TOKEN_ID.fullmatch(minimal["id"] + "-conventions-99"))

    def test_the_runtime_config_agrees_with_the_markers_and_the_items(self):
        runtime = self.docs["formal-companion/informalize-runtime"]
        markers = self.docs["formal-companion/informalize-markers"]
        items = self.docs["formal-companion/informalize-items"]
        self.assertEqual(runtime["boxes"], markers["boxes_config"])
        self.assertEqual(runtime["job"], markers["job"])
        self.assertEqual(runtime["library"]["commit"], items["library"]["commit"])
        for package in ("zref-savepos.sty", "zref-abspage.sty", "amsthm.sty", "keyval.sty"):
            self.assertIn(package, runtime["tex_packages"])   # what fcmarkers.sty loads

    def test_the_items_example_keeps_every_rule(self):
        self.assertEqual(items_problems(self.docs["formal-companion/informalize-items"]), [])

    def test_a_broken_items_table_is_refused(self):
        good = self.docs["formal-companion/informalize-items"]
        breaks = {
            "a gap in the ids": lambda d: d["items"][1].update(id="1.3"),
            "a key in no part": lambda d: d["items"][1]["declarations"][0].update(part=None),
            "a part without a key": lambda d: d["items"][1]["declarations"][1].update(role="helper"),
            "parts out of order": lambda d: d["items"][1]["parts"].reverse(),
            "an unknown relation": lambda d: d["items"][0]["declarations"][0].update(relation="sameAs"),
            "an unexplained broadMatch": lambda d: d["items"][0]["declarations"][0].update(
                relation="broadMatch"),
            "an unknown reference source": lambda d: d["items"][0]["reference"].update(source="book"),
            "a closeMatch, which the legacy export cannot carry":
                lambda d: d["items"][0]["declarations"][0].update(relation="closeMatch"),
        }
        for what, mutate in breaks.items():
            with self.subTest(broken=what):
                doc = copy.deepcopy(good)
                mutate(doc)
                self.assertTrue(items_problems(doc), what)

    def test_a_withdrawn_id_stays_a_gap(self):
        doc = copy.deepcopy(self.docs["formal-companion/informalize-items"])
        doc["items"][1]["id"] = "1.3"
        self.assertTrue(items_problems(doc))
        doc["decisions"].append({"withdrawn": "1.2", "why": "merged into 1.1"})
        self.assertEqual(items_problems(doc), [])

    def test_the_markers_example_keeps_every_rule(self):
        doc = self.docs["formal-companion/informalize-markers"]
        self.assertEqual(markers_problems(doc), [])
        broken = copy.deepcopy(doc)
        broken["entries"][1]["boxes"][0][1] = 0.9                # x + w > 1
        self.assertTrue(markers_problems(broken))

    def test_the_markers_and_the_items_describe_one_item(self):
        markers = self.docs["formal-companion/informalize-markers"]
        items = {i["id"]: i for i in self.docs["formal-companion/informalize-items"]["items"]}
        for entry in markers["entries"]:
            if "parent" in entry:
                item = items[entry["parent"]]
                keys = {d["name"] for d in item["declarations"] if d["part"] == entry["part"]}
                self.assertTrue(set(entry["decls"]) <= keys, entry["id"])
            else:
                item = items[entry["id"]]
                self.assertEqual(entry["parts"], [f"{entry['id']}({p['letter']})" for p in item["parts"]])
                self.assertEqual((entry["kind"], entry["title"]), (item["kind"], item["title"]))

    def test_the_attack_and_ruling_examples_join(self):
        attack = self.docs["formal-companion/informalize-attack"]
        ruling = self.docs["formal-companion/informalize-ruling"]
        self.assertIn(attack["direction"], DIRECTIONS)
        ids_ = {f"{attack['task']}#{i}" for i in range(len(attack["findings"]))}
        for finding in attack["findings"]:
            self.assertIn(finding["severity"], SEVERITIES)
            self.assertIn(finding["item"], attack["checked_items"])
            self.assertTrue(finding["quote"] and finding["declarations"])
        for row in ruling["rulings"]:
            self.assertIn(row["finding"], ids_)
            self.assertIn(row["ruling"], RULINGS)
            self.assertIn(row["action"], ACTIONS)
            self.assertEqual(row["action"] == "none", row["ruling"] == "not_real")
            if row["action"] == "out_of_scope":
                self.assertTrue(any(r["finding"] == row["finding"] for r in ruling["requests"]))
        self.assertEqual(attack["section"], ruling["section"])
        # both stamp the commit they read; a judge run plans from its attack run's
        self.assertRegex(attack["base_commit"], r"\A[0-9a-f]{40}\Z")
        self.assertEqual(attack["base_commit"], ruling["base_commit"])

    def test_the_hypotheses_example_names_macros(self):
        doc = self.docs["formal-companion/informalize-hypotheses"]
        keys = [h["key"] for h in doc["hypotheses"]]
        self.assertEqual(len(keys), len(set(keys)))
        for row in doc["hypotheses"]:
            self.assertRegex(row["macro"], r"\A\\[A-Za-z]+\Z")
            self.assertTrue(row.get("binder_head") or row.get("binder_type"))


class TestTheRuntimeItSpecifies(unittest.TestCase):
    """Section 7 against templates/informalize/runtime/ (component 2): the .aux records the
    marker package writes, the tools that read them, and the tools' flags."""

    RUNTIME = SKILL / "templates" / "informalize" / "runtime"

    def records(self) -> dict[str, int]:
        """``{name: arity}`` of the tools' .aux parser, read without importing it."""
        tree = ast.parse((self.RUNTIME / "tools" / "fccommon.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "RECORDS" for t in node.targets):
                return ast.literal_eval(node.value)
        raise AssertionError("fccommon.py defines no RECORDS")

    def test_the_aux_records_have_one_arity_everywhere(self):
        sty = (self.RUNTIME / "fcmarkers.sty").read_text(encoding="utf-8")
        defined = {name: len(args) // 2 for name, args in
                   re.findall(r"^\\def\\(fc@aux[a-z]+)((?:#\d)*)\{", sty, re.M)}
        records = {k: v for k, v in self.records().items() if k.startswith("fc@aux")}
        # \fc@auxkind is read back by \itemref, not by the tools
        self.assertEqual(records, {k: v for k, v in defined.items() if k != "fc@auxkind"})
        spelled = re.findall(r"`\\(fc@aux[a-z]+)((?:\{[^{}`]*\})+)", section("7.2"))
        self.assertTrue(spelled)
        for name, groups in spelled:
            with self.subTest(record=name):
                self.assertEqual(groups.count("{"), defined[name], groups)

    def test_the_tools_flags_are_the_ones_it_lists(self):
        text = section("7.3")
        build = (self.RUNTIME / "tools" / "build_pdf.sh").read_text(encoding="utf-8")
        listed = set(re.findall(r"\[(--[a-z-]+)", re.search(r"`tools/build_pdf\.sh ([^`]*)`",
                                                              text).group(1)))
        handled = set(re.findall(r"^\s+(--[a-z-]+)\)", build, re.M)) - {"--help"}
        self.assertEqual(listed, handled)
        for tool in ("check_items", "proof_sheets", "extract_markers"):
            with self.subTest(tool=tool):
                source = (self.RUNTIME / "tools" / f"{tool}.py").read_text(encoding="utf-8")
                usage = re.search(rf"`tools/{tool}\.py ([^`]*)`", text).group(1)
                listed = set(re.findall(r"\[(--[a-z-]+)", usage))
                handled = set(re.findall(r'add_argument\("(--[a-z-]+)"', source))
                self.assertEqual(listed, handled)


class TestEachPromiseIsOneList(unittest.TestCase):
    """The subcommands, phases and gates the specification names, and the code once it exists."""

    def test_every_informalize_subcommand_it_writes_is_on_the_list(self):
        written: set[str] = set()
        for group in re.findall(r"cf informalize ([a-z][a-z-]*(?: \| [a-z][a-z-]*)*)", SPEC):
            written |= {word.strip() for word in group.split("|")}
        self.assertEqual(written, set(SUBCOMMANDS))

    def test_every_phase_it_plans_is_on_the_list(self):
        planned = set(re.findall(r"--phase ([a-z]+)", SPEC))
        self.assertTrue(planned <= set(PHASES), planned - set(PHASES))
        table = section("8.2")
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.assertIn(f"| `{phase}` |", table)
        self.assertIn(", ".join(f"`{p}`" for p in PHASES[:-1]) + f" or `{PHASES[-1]}`", FLAT)

    def test_every_gate_it_approves_is_on_the_list(self):
        gates = set(re.findall(r"(?:cf informalize |`)approve (?:<id>|[a-z0-9-]+) ([a-z][a-z-]*)",
                               SPEC))
        self.assertEqual(gates, set(APPROVAL_GATES))
        table = section("13")
        for gate in APPROVAL_GATES:
            with self.subTest(gate=gate):
                self.assertIn(f"| {gate.replace('-', ' ')} |", table)

    def test_the_cli_once_built_is_the_list(self):
        tree = cli_tree()
        if "informalize" not in tree:
            self.skipTest("cf informalize is not built yet (component 3)")
        built = subcommands_of(tree["informalize"])
        self.assertTrue(built <= set(SUBCOMMANDS), built - set(SUBCOMMANDS))
        for component in sorted(set(SUBCOMMANDS.values())):
            names = {n for n, c in SUBCOMMANDS.items() if c == component}
            with self.subTest(component=component):
                self.assertIn(built & names, (set(), names),
                              f"component {component} builds {sorted(names)}, all or none")

    def test_the_planner_once_built_plans_the_phases(self):
        tree = cli_tree()
        if "plan-informalize" not in tree:
            self.skipTest("cf plan-informalize is not built yet (component 5a)")
        choices = [a.choices for a in tree["plan-informalize"]._actions   # noqa: SLF001
                   if "--phase" in a.option_strings]
        self.assertEqual(len(choices), 1)
        self.assertEqual(tuple(choices[0]), PHASES)

    def test_the_vocabulary_once_built_is_the_list(self):
        pins = {"INFORMALIZE_PHASES": PHASES, "APPROVAL_GATES": APPROVAL_GATES,
                "INFORMALIZE_KINDS": KINDS, "INFORMALIZE_ROLES": ROLES,
                "INFORMALIZE_RELATIONS": RELATIONS, "REFERENCE_SOURCES": REFERENCE_SOURCES}
        present = {name: getattr(vocab, name) for name in pins if hasattr(vocab, name)}
        if not present:
            self.skipTest("the informalize vocabulary is not in cflib.vocab yet (component 3)")
        for name, value in present.items():
            with self.subTest(name=name):
                self.assertIn(name, SPEC)
                self.assertEqual(tuple(value), pins[name])


class TestTheRuleException(unittest.TestCase):
    """Section 1.3 is the wording; component 3 writes it where the rule lives."""

    def test_the_wording_names_the_repository_and_its_limits(self):
        quoted = "\n".join(line[2:] for line in section("1.3").splitlines() if line.startswith("> "))
        flat = " ".join(quoted.split())
        for needle in ("`informalize/<id>/repo/`", "`cflib.gitsafe`", "symlink",
                       "never write to the library", "`cf overlay` keeps its own exception"):
            with self.subTest(needle=needle):
                self.assertIn(needle, flat)

    def test_once_built_the_rule_says_so_where_it_lives(self):
        if "informalize" not in cli_tree():
            self.skipTest("cf informalize is not built yet (component 3)")
        for name in ("SKILL.md", "CLAUDE.md", "CONTRIBUTING.md", "docs/overlay.md",
                     "docs/design/stage_map.md"):
            with self.subTest(document=name):
                self.assertIn("informalize/<id>/repo/", (SKILL / name).read_text(encoding="utf-8"))


class TestTheFirstTargetIsOnlyAnExample(unittest.TestCase):
    """The mode is generic: its first theorem lives in the acceptance section and nowhere in code.

    The names are read from section 12's `cf informalize init` line, so this file spells none
    of them: the library prefix, the theorem's last component, the reference work's first
    author, and every component of the module that the specification uses nowhere else.
    """

    @staticmethod
    def init_flags() -> dict[str, str]:
        (line,) = [ln for ln in section("12").splitlines() if ln.startswith("cf informalize init ")]
        return {flag: value.strip('"') for flag, value in
                re.findall(r'--([a-z-]+) ("[^"]*"|\S+)', line)}

    def needles(self) -> tuple[list[str], list[str]]:
        """``(in the specification, in code)``: what may appear in section 12 only, and nowhere in code."""
        flags = self.init_flags()
        prefix, theorem, module = flags["prefix"], flags["theorem"], flags["module"]
        author = flags["reference"].split(",")[0].split()[-1]
        start, end = SPEC.index("\n## 12. "), SPEC.index("\n## 13. ")
        outside = SPEC[:start] + SPEC[end:]
        parts = [c for c in module.split(".")[1:] if c not in outside]
        last = theorem.rsplit(".", 1)[-1]
        code = [last, *parts]
        self.assertTrue(parts, "no component of the module is particular to the first target")
        return [prefix, author, *code], code

    def test_the_specification_names_it_only_in_the_acceptance_section(self):
        start = SPEC.index("\n## 12. ")
        end = SPEC.index("\n## 13. ")
        for needle in self.needles()[0]:
            for match in re.finditer(re.escape(needle), SPEC):
                with self.subTest(needle=needle, at=match.start()):
                    self.assertTrue(start < match.start() < end,
                                    f"{needle} outside section 12: "
                                    f"{SPEC[max(0, match.start() - 40):match.start() + 40]!r}")

    def test_no_code_template_or_test_names_it(self):
        needles = self.needles()[1]
        here = Path(__file__).resolve()
        for root in ("scripts", "templates", "tests"):
            for path in sorted((SKILL / root).rglob("*")):
                if not path.is_file() or "vendor" in path.parts or path.resolve() == here:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for needle in needles:
                    with self.subTest(path=str(path.relative_to(SKILL)), needle=needle):
                        self.assertNotIn(needle, text)


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
