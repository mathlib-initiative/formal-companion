"""The informalize fleet: the gate, the planner, the bridge's switch and the attempt cap.

New code, for docs/proposals/informalize.md sections 8.1 to 8.3 (component 5a of its section
11).  The whole fleet on a fake library, with the gate run as the host runs it, is
tests/test_e2e_informalize_synthetic.py; this module holds the parts to their rules one at a
time:

* the gate's copies of the vocabulary are cflib's, and it stands alone (standard library and
  its siblings only; a missing sibling is exit 3, never a verdict);
* the document rules (6 and 7) on the specification's own attack and ruling examples, and
  each broken copy refused under its rule; the lint of rule 2; rule 9's word runs; the task
  file's status line;
* the planner's tasks, owned paths, wrappers and layout, its refusals and its determinism;
* the bridge's second switch: an informalize reviewer's git is read-only, its gate entry is
  the `--findings` call, and the tamper guard undoes what it changes.
"""
from __future__ import annotations

try:
    import _hermetic  # noqa: F401
except ImportError:  # pragma: no cover
    from tests import _hermetic  # noqa: F401

import ast
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
for _entry in (str(HERE), str(SKILL / "scripts"), str(SKILL / "scripts" / "gate")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import _informalize_doc as doc  # noqa: E402
import test_locate_review_turns as tlr  # noqa: E402
from cflib import vocab  # noqa: E402
from informalize import plan as iplan  # noqa: E402
from run import attempt_cap  # noqa: E402

import verify_informal as vi  # noqa: E402

SPEC = (SKILL / "docs" / "proposals" / "informalize.md").read_text(encoding="utf-8")
DRIVER = HERE / "e2e" / "informalize_synthetic" / "driver.py"


def spec_examples() -> dict[str, dict]:
    out = {}
    for body in re.findall(r"^```json\n(.*?)^```", SPEC, re.M | re.S):
        data = json.loads(body)
        out[data["schema"]] = data
    return out


def scratch(case: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="cf-fleet-"))
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path.resolve()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# the gate's copies, and the gate on its own

class TestTheCopies(unittest.TestCase):

    def test_the_gate_s_vocabulary_is_cflib_s(self):
        for mine, theirs in ((vi.PHASES, vocab.INFORMALIZE_PHASES),
                             (vi.SEVERITIES, vocab.INFORMALIZE_SEVERITIES),
                             (vi.DIRECTIONS, vocab.INFORMALIZE_DIRECTIONS),
                             (vi.RULINGS, vocab.INFORMALIZE_RULINGS),
                             (vi.ACTIONS, vocab.INFORMALIZE_ACTIONS),
                             (vi.ANSWERS, vocab.INFORMALIZE_ANSWERS),
                             (vi.ATTEMPT_CAP, vocab.INFORMALIZE_ATTEMPT_CAP),
                             (vi.BUILD_TIMEOUT, vocab.INFORMALIZE_BUILD_TIMEOUT),
                             (vi.COPY_RUN_WORDS, vocab.INFORMALIZE_COPY_RUN_WORDS)):
            with self.subTest(value=theirs):
                self.assertEqual(mine, theirs)

    def test_the_planner_the_gate_and_the_cap_name_one_schema(self):
        self.assertEqual(vi.TASK_SCHEMA, iplan.TASK_SCHEMA)
        self.assertEqual(attempt_cap.INFORMALIZE_TASK_SCHEMA, iplan.TASK_SCHEMA)
        self.assertEqual(vi.GATE_SCHEMA, iplan.GATE_SCHEMA)
        self.assertEqual(vi.CONVENTION_FILES, iplan.CONVENTION_FILES)
        self.assertEqual(vi.ATTEMPT_CAP, vocab.LOCATE_ATTEMPT_CAP)

    def test_the_document_schemas_are_the_specification_s(self):
        names = set(spec_examples())
        self.assertIn(vi.ATTACK_SCHEMA, names)
        self.assertIn(vi.RULING_SCHEMA, names)

    def test_the_specification_numbers_the_rules_the_gate_numbers(self):
        rules = re.findall(r"^(\d)\. \*\*([A-Z][a-z]+)", SPEC.split("### 8.3", 1)[1]
                           .split("### 8.4", 1)[0], re.M)
        self.assertEqual([int(n) for n, _ in rules], list(range(1, 10)))
        head = vi.__doc__.split("The rules", 1)[1]
        for number, name in rules:
            with self.subTest(rule=number):
                self.assertIn(f"{number}. **{name}", head)


class TestTheGateStandsAlone(unittest.TestCase):

    def test_it_imports_the_standard_library_and_its_siblings(self):
        tree = ast.parse((SKILL / "scripts" / "gate" / "verify_informal.py")
                         .read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        allowed = set(sys.stdlib_module_names) | {"__future__", "verify_findings", "fccommon",
                                                  "fcitems"}
        self.assertLessEqual(names, allowed)

    def test_a_gate_directory_without_a_sibling_is_exit_3(self):
        tmp = scratch(self)
        shutil.copy2(SKILL / "scripts" / "gate" / "verify_informal.py", tmp)
        shutil.copytree(SKILL / "templates" / "informalize" / "runtime", tmp / "tex",
                        ignore=shutil.ignore_patterns("__pycache__"))
        done = subprocess.run([sys.executable, "-I", str(tmp / "verify_informal.py"),
                               "--changed-files"], capture_output=True, text=True, cwd=str(tmp),
                              env={"PATH": "/usr/bin:/bin", "HOME": str(tmp)})
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("verify_findings.py", done.stderr)
        self.assertFalse(list(tmp.rglob("__pycache__")))

    def test_a_refused_command_line_is_exit_3(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        for argv in (["--pre-merge"], ["--changed-files", "--findings", "x.json"],
                     ["--gate", "x"]):
            with self.subTest(argv=argv):
                out = io.StringIO()
                with redirect_stdout(out), redirect_stderr(out):
                    self.assertEqual(vi.main(argv), 3)
                self.assertIn("could not run", out.getvalue())


# ---------------------------------------------------------------------------
# rules 6 and 7, on the specification's examples

def make_gate(tmp: Path, *, phase: str, manifests: list[dict], data: dict[str, dict],
              base_files: dict[str, str], base_commit: str, attack_base: str | None = None) -> Path:
    """A gate directory of pinned files and its gate.json; returns gate.json's path."""
    gate = tmp / "gate"
    files: dict[str, bytes] = {
        "declarations.json": json.dumps(doc.DECLARATIONS).encode("utf-8")}
    for manifest in manifests:
        files[f"tasks/{manifest['task_id']}.json"] = json.dumps(manifest).encode("utf-8")
    for rel, value in data.items():
        files[f"data/{rel}"] = json.dumps(value).encode("utf-8")
    for rel, raw in files.items():
        (gate / rel).parent.mkdir(parents=True, exist_ok=True)
        (gate / rel).write_bytes(raw)
    config = {"schema": vi.GATE_SCHEMA, "id": "example-thm", "run_id": "example-thm-x-1",
              "phase": phase, "base_commit": base_commit, "attack_base_commit": attack_base,
              "library": {"commit": doc.COMMIT, "src": str(tmp / "src")},
              "base_files": base_files, "files": {rel: sha(raw) for rel, raw in files.items()},
              "log_dir": str(tmp / "log"), "orchestrator_db": None, "attempts_dir": None}
    (gate / "gate.json").write_text(json.dumps(config), encoding="utf-8")
    return gate / "gate.json"


SECTION = "informal/sections/01-gluing-spaces.tex"


class DocumentCase(unittest.TestCase):

    def setUp(self):
        self.tmp = scratch(self)
        examples = spec_examples()
        self.attack = examples[vi.ATTACK_SCHEMA]
        self.ruling = examples[vi.RULING_SCHEMA]
        self.table = examples["formal-companion/informalize-items"]
        self.text = (b"Let $U$ and $V$ be open.\n\\itempart{b}{Then the induced map is "
                     b"surjective.}\n")
        self.files = {"informal/items.json": json.dumps(self.table).encode("utf-8"),
                      SECTION: self.text}
        self.base_files = {rel: sha(raw) for rel, raw in self.files.items()}
        self.base = self.attack["base_commit"]

    def manifest(self, task: str, **extra) -> dict:
        return {"schema": vi.TASK_SCHEMA, "task_id": task, "run_id": "example-thm-x-1",
                "section": "1", "section_file": SECTION, "items": ["1.1", "1.2"], **extra}

    def problems(self, check) -> list[str]:
        problems = vi.Problems()
        check(problems)
        return problems.lines()


class TestTheAttackRule(DocumentCase):

    def setUp(self):
        super().setUp()
        self.man = self.manifest("attack-lean-01", direction="lean-first",
                                 paths=["findings/attack-lean-01/attack.json"])
        self.cfg = vi.GateConfig.load(make_gate(self.tmp, phase="attack", manifests=[self.man],
                                                data={}, base_files=self.base_files,
                                                base_commit=self.base))

    def check(self, document) -> list[str]:
        return self.problems(lambda p: vi.check_attack(self.cfg, self.man, document,
                                                       self.files, p))

    def test_the_example_passes(self):
        self.assertEqual(self.check(self.attack), [])

    def test_each_broken_copy_is_refused_under_rule_6(self):
        def finding(**change):
            out = copy.deepcopy(self.attack)
            out["findings"][0].update(change)
            return out
        cases = {
            "quote": finding(quote="the induced map is onto"),
            "severity": finding(severity="minor"),
            "declarations": finding(declarations=["ExampleLib.nowhere"]),
            "part": finding(part="c"),
            "item": finding(item="2.1"),
            "checked_items": dict(self.attack, checked_items=["1.2"]),
            "direction": dict(self.attack, direction="tex-first"),
            "base_commit": dict(self.attack, base_commit="0" * 40),
            "unknown key": dict(self.attack, verdict="ok"),
        }
        for what, document in cases.items():
            with self.subTest(what=what):
                found = self.check(document)
                self.assertTrue(found and all(line.startswith("[6]") for line in found), found)

    def test_a_section_file_other_than_the_base_s_is_refused(self):
        self.files[SECTION] = self.text + b"%\n"
        self.assertTrue(any("base text" in line for line in self.check(self.attack)))


class TestTheJudgeRule(DocumentCase):

    def setUp(self):
        super().setUp()
        self.man = self.manifest("judge-01", attacks=["attack-lean-01"],
                                 paths=[SECTION, "findings/judge-01/ruling.json"])
        self.cfg = vi.GateConfig.load(make_gate(
            self.tmp, phase="judge", manifests=[self.man],
            data={"attacks/attack-lean-01.json": self.attack}, base_files=self.base_files,
            base_commit="1" * 40, attack_base=self.base))

    def check(self, document, changed=True) -> list[str]:
        return self.problems(lambda p: vi.check_ruling(self.cfg, self.man, document, changed, p))

    def test_the_example_passes(self):
        self.assertEqual(self.check(self.ruling), [])

    def test_each_broken_copy_is_refused_under_rule_7(self):
        def row(**change):
            out = copy.deepcopy(self.ruling)
            out["rulings"][0].update(change)
            return out
        request = {"finding": "attack-lean-01#0", "file": "informal/notation.sty",
                   "what": "Name the hypothesis.", "why": "It is shared."}
        cases = {
            "no ruling": dict(self.ruling, rulings=[]),
            "unknown finding": row(finding="attack-lean-01#7"),
            "none for a real one": row(action="none"),
            "out of scope without a request": row(action="out_of_scope"),
            "a request for a fixed one": dict(self.ruling, requests=[request]),
            "base commit": dict(self.ruling, base_commit="1" * 40),
            "twice": dict(self.ruling, rulings=self.ruling["rulings"] * 2),
        }
        for what, document in cases.items():
            with self.subTest(what=what):
                found = self.check(document)
                self.assertTrue(found and all(line.startswith("[7]") for line in found), found)
        self.assertTrue(self.check(self.ruling, changed=False))
        self.assertEqual(self.check(self.ruling, changed=None), [])
        moved = row(action="out_of_scope")
        moved["requests"] = [request]
        self.assertEqual(self.check(moved), [])
        moved["requests"] = [dict(request, file=SECTION)]
        self.assertTrue(any("own section file" in line for line in self.check(moved)))


class TestTheEditRule(DocumentCase):

    def setUp(self):
        super().setUp()
        self.ruling_doc = copy.deepcopy(self.ruling)
        self.ruling_doc["rulings"][0]["action"] = "out_of_scope"
        self.ruling_doc["requests"] = [{"finding": "attack-lean-01#0",
                                        "file": "informal/sections/02-covers.tex",
                                        "what": "x", "why": "y"}]
        self.man = {"schema": vi.TASK_SCHEMA, "task_id": "edit", "run_id": "example-thm-x-1",
                    "rulings": ["judge-01"], "paths": []}
        self.cfg = vi.GateConfig.load(make_gate(
            self.tmp, phase="edit", manifests=[self.man],
            data={"rulings/judge-01.json": self.ruling_doc}, base_files={},
            base_commit="1" * 40))

    def test_every_request_is_answered_once(self):
        good = {"task": "edit", "answers": [{"finding": "attack-lean-01#0", "status": "done",
                                             "reason": "Applied."}]}
        run = lambda d, s=None: self.problems(  # noqa: E731
            lambda p: vi.check_edit(self.cfg, self.man, d, s, p))
        self.assertEqual(run(good, {"informal/sections/02-covers.tex"}), [])
        self.assertTrue(run({"task": "edit", "answers": []}))
        self.assertTrue(run(dict(good, answers=good["answers"] * 2)))
        self.assertTrue(run({"task": "edit", "answers": [dict(good["answers"][0],
                                                               status="maybe")]}))
        self.assertTrue(any("no request" in line for line in
                            run(good, {"informal/sections/01-gluing-spaces.tex"})))

    def test_a_finding_s_requests_are_answered_once_and_each_names_a_file(self):
        """A fix that spans two files is two requests for one finding: rule 7 takes them,
        and rule 8 lets the editor change either file, with one answer for the finding."""
        two = [dict(self.ruling_doc["requests"][0]),
               dict(self.ruling_doc["requests"][0], file="informal/sections/00-conventions.tex")]
        man = self.manifest("judge-01", attacks=["attack-lean-01"])
        judge = vi.GateConfig.load(make_gate(
            self.tmp / "judge", phase="judge", manifests=[man],
            data={"attacks/attack-lean-01.json": self.attack}, base_files=self.base_files,
            base_commit="1" * 40, attack_base=self.base))
        self.assertEqual(self.problems(lambda p: vi.check_ruling(
            judge, man, dict(self.ruling_doc, requests=two), True, p)), [])
        good = {"task": "edit", "answers": [{"finding": "attack-lean-01#0", "status": "done",
                                             "reason": "Applied in both files."}]}
        for order in (two, two[::-1]):
            ruling = dict(self.ruling_doc, requests=order)
            cfg = vi.GateConfig.load(make_gate(
                self.tmp / f"edit-{order[0]['file'][-8:]}", phase="edit", manifests=[self.man],
                data={"rulings/judge-01.json": ruling}, base_files={}, base_commit="1" * 40))
            with self.subTest(first=order[0]["file"]):
                self.assertEqual(self.problems(lambda p: vi.check_edit(
                    cfg, self.man, good, {"informal/sections/02-covers.tex"}, p)), [])
                twice = dict(good, answers=good["answers"] * 2)
                self.assertTrue(self.problems(lambda p: vi.check_edit(cfg, self.man, twice,
                                                                      set(), p)))

    def test_a_proposal_names_an_item_and_says_why(self):
        run = lambda d: self.problems(  # noqa: E731
            lambda p: vi.check_proposal(self.man, d, self.table, p))
        good = {"task": "edit", "changes": [{"item": "1.1", "field": "title", "from": "a",
                                             "to": "b", "why": "Clearer."}]}
        self.assertEqual(run(good), [])
        self.assertTrue(run({"task": "edit", "changes": [dict(good["changes"][0], item="9.9")]}))
        self.assertTrue(run({"task": "edit", "changes": [{"item": "1.1", "field": "title"}]}))


# ---------------------------------------------------------------------------
# rule 2's lint, rule 9's runs, rule 1's task file

class TestTheLint(unittest.TestCase):

    def lint(self, text: str) -> list[str]:
        problems = vi.Problems()
        vi.lint("informal/sections/01-x.tex", text, problems)
        return problems.lines()

    def test_what_could_forge_a_record_is_refused(self):
        record = "{1.1}{}{ExampleLib.GlueData}"
        for text in ("\\makeatletter", "\\fc@auxitem{1.1}", "\\immediate\\write18{x}",
                     "\\openout3=x", "\\def\\leanref#1{}", "\\gdef\\x{}", "\\let\\a\\b",
                     "\\csname x\\endcsname", "\\input{/etc/passwd}", "\\include{x}",
                     "\\renewcommand\\fcref{}", "\\catcode`\\@=11", "\\special{x}",
                     "\\zsavepos{fc:1.1:start}", "\\expandafter\\x",
                     # TeX's ^^ notation spells @ and the linted letters
                     "\\makeat^^6cetter\\wr^^69te\\^^40auxout{x}",
                     # expl3 needs no @, \write or \csname
                     "\\ExplSyntaxOn\\iow_now:cx{ @auxout }{x}\\ExplSyntaxOff",
                     # a % that \verb or \url makes text hides nothing from the lint
                     "\\verb|%|\\makeatletter", "\\url{%}\\makeatletter",
                     # a record spelled into a label's key, or written by name
                     "\\label{\\string\\fc @auxleanref" + record + "}",
                     "\\label{\\detokenize{\\\\}fc@auxleanref" + record + "}",
                     "\\UseName{fc@auxwrite}{x}", "\\newlinechar=13",
                     "\\DeclareRobustCommand\\leanref[1]{}", "\\NewCommandCopy\\x\\leanref",
                     "\\RenewDocumentCommand\\fcref{m}{}", "\\renewenvironment{theorem}{}{}",
                     "\\AddToHook{begindocument}{x}", "\\AtEndDocument{x}",
                     "\\pdfescapestring{x}",
                     "% \\makeatletter in a comment"):
            with self.subTest(text=text):
                found = self.lint(text)
                self.assertTrue(found and found[0].startswith("[2]"), found)

    def test_the_marker_commands_comments_and_labels_pass(self):
        text = ("\\begin{theorem}[id=1.2]{T}\\fcref*{Theorem 1}\\itempart{a}{x\\leanref{A.b}}"
                "\\hypOpen* \\label{eq:1.2-one} 100\\% sure % a comment\n"
                "\\newcommand\\Glue{G}\\DeclareMathOperator{\\Hom}{Hom}$\\pi_1(X)$"
                "$\\csc x$\\end{theorem}")
        self.assertEqual(self.lint(text), [])

    def test_a_label_carries_its_own_section(self):
        for path, key, ok in (("informal/sections/01-x.tex", "eq:1.2-square", True),
                              ("informal/sections/12-x.tex", "eq:12.3-a", True),
                              ("informal/sections/01-x.tex", "eq:main", False),
                              ("informal/sections/01-x.tex", "eq:2.1-square", False),
                              ("informal/sections/01-x.tex", "item:1.1", False),
                              ("informal/sections/00-conventions.tex", "sec:conventions", True),
                              ("informal/sections/00-conventions.tex", "eq:1.2-square", False),
                              ("informal/notation.sty", "sec:2", False)):
            with self.subTest(path=path, key=key):
                problems = vi.Problems()
                vi.lint(path, f"\\begin{{equation}}\\label{{{key}}}x\\end{{equation}}", problems)
                self.assertEqual(len(problems) == 0, ok, problems.lines())

    def test_the_templates_and_the_fixture_s_text_pass(self):
        texts = {"informal/sections/01-gluing-spaces.tex": doc.SECTION_1,
                 "informal/sections/02-covers.tex": doc.SECTION_2,
                 "informal/notation.sty": doc.NOTATION_HYPOTHESES}
        project = SKILL / "templates" / "informalize" / "project"
        for rel in vi.CONVENTION_FILES:            # the template files a task changes
            path = project / rel.removeprefix("informal/")
            if path.suffix in (".tex", ".sty"):
                texts[rel] = path.read_text(encoding="utf-8")
        for rel, text in texts.items():
            with self.subTest(rel=rel):
                problems = vi.Problems()
                vi.lint(rel, text, problems)
                self.assertEqual(problems.lines(), [])


class TestCopying(unittest.TestCase):

    def test_words_drop_math_commands_and_comments(self):
        self.assertEqual(vi.tex_words("Let $U \\cup V$ be \\emph{open} sets. % a note"),
                         ["let", "be", "open", "sets"])
        self.assertEqual(vi.comment_words(["/-- The map `f` is $C^1$ smooth. -/",
                                           "theorem x : True -- trivial"]),
                         [["the", "map", "is", "smooth"], ["trivial"]])

    def test_a_run_of_the_bound_is_refused_and_one_short_of_it_is_not(self):
        tmp = scratch(self)
        words = "one two three four five six seven eight nine ten eleven twelve".split()
        (tmp / "src" / "A").mkdir(parents=True)
        (tmp / "src" / "A" / "B.lean").write_text("/-- " + " ".join(words) + " -/\n"
                                                  "theorem t : True := trivial\n",
                                                  encoding="utf-8")
        cfg = object.__new__(vi.GateConfig)
        cfg.copy_run_words, cfg.library_src = 12, tmp / "src"
        cfg.declarations = {"A.t": {"name": "A.t", "path": "A/B.lean"}}
        table = {"items": [{"id": "1.1", "in_v1": True, "declarations": [{"name": "A.t"}]}]}
        for text, refused in ((" ".join(words), True), (" ".join(words[:11]), False),
                              ("One two three four five six seven eight nine ten eleven "
                               "$x$ twelve", True)):
            with self.subTest(text=text):
                problems = vi.Problems()
                vi.check_copying(cfg, {"informal/sections/01-x.tex": text}, ["1.1"], table,
                                 problems)
                self.assertEqual(bool(len(problems)), refused, problems.lines())


class TestTheTaskFile(unittest.TestCase):

    def test_only_the_status_line_may_change(self):
        tmp = scratch(self)
        before = "id: draft-01\nstatus: open\npriority: high\n"
        path = tmp / "t.yaml"
        for after, ok in (("id: draft-01\nstatus: complete\npriority: high\n", True),
                          ("id: draft-01\nstatus: complete\npriority: low\n", False),
                          ("id: draft-01\nstatus: complete\nstatus: open\npriority: high\n",
                           False),
                          ("id: draft-01\nstatus: {a: 1}\npriority: high\n", False)):
            with self.subTest(after=after):
                path.write_text(after, encoding="utf-8")
                self.assertEqual(vi.task_file_change(before, path) == "", ok)


# ---------------------------------------------------------------------------
# the planner

class TestThePlannersPieces(unittest.TestCase):

    def test_each_phase_owns_its_paths(self):
        table = copy.deepcopy(doc.ITEMS)
        cases = {
            "items": ["items"], "conventions": ["conventions"],
            "draft": ["draft-01", "draft-02"],
            "attack": ["attack-lean-01", "attack-tex-01", "attack-lean-02", "attack-tex-02"],
            "judge": ["judge-01", "judge-02"], "edit": ["edit"],
        }
        for phase, ids in cases.items():
            with self.subTest(phase=phase):
                tasks = iplan.make_tasks(phase, table, ["1", "2"],
                                         attacks={"1": ["attack-lean-01"]},
                                         rulings=["judge-01"],
                                         request_files=["informal/sections/02-covers.tex"])
                self.assertEqual([t.id for t in tasks], ids)
                owned = [p for t in tasks for p in t.paths]
                self.assertEqual(len(owned), len(set(owned)), "two tasks own one path")
                for task in tasks:
                    self.assertLessEqual(set(task.required), set(task.paths))
                    for path in task.paths:
                        self.assertTrue(path.startswith(("informal/", f"findings/{task.id}/")),
                                        path)
        draft = iplan.make_tasks("draft", table, ["2"])[0]
        self.assertEqual((draft.paths, draft.items), (["informal/sections/02-covers.tex"],
                                                      ["2.1", "2.2", "2.3"]))
        edit = iplan.make_tasks("edit", table, [], rulings=["judge-01"],
                                request_files=["informal/sections/02-covers.tex"])[0]
        self.assertIn("informal/sections/02-covers.tex", edit.paths)
        self.assertNotIn("informal/items.json", edit.paths)

    def test_the_wrappers_give_each_role_its_tools(self):
        env = iplan.wrapper_env("attack", "/h/r-gate")
        gate = "python3 /h/r-gate/verify_informal.py --gate-config /h/r-gate/gate.json"
        self.assertEqual(env["reviewer"]["CF_REVIEWER_TOOLS"].split(","),
                         ["Read", "Glob", "Grep", "Bash(git diff:*)", "Bash(git show:*)",
                          "Bash(git log:*)", "Bash(git status:*)", f"Bash({gate} --findings:*)"])
        self.assertEqual(env["worker"]["CF_EXTRA_TOOLS"],
                         "Bash(bash /h/r-gate/tex/tools/build_pdf.sh:*),"
                         "Bash(python3 /h/r-gate/tex/tools/check_items.py:*)")
        for role in ("worker", "reviewer"):
            self.assertEqual(env[role]["CF_ATTEMPT_CAP"], str(vocab.INFORMALIZE_ATTEMPT_CAP))
            self.assertEqual(env[role]["CF_TASK_MANIFESTS"], "/h/r-gate/tasks")
        self.assertEqual(env["worker"]["CF_FINDINGS_GLOB"], "findings/${TEND_TASK_ID:-*}/*.json")
        self.assertEqual(iplan.wrapper_env("draft", "/g")["worker"]["CF_FINDINGS_GLOB"],
                         iplan.NO_RESULT_GLOB)
        self.assertEqual(iplan.wrapper_env("edit", "/g")["worker"]["CF_FINDINGS_GLOB"],
                         "findings/edit/*.json")

    def test_the_host_layout_is_under_the_remote_root(self):
        from cflib.paths import WorkspacePaths

        layout = iplan.host_layout(WorkspacePaths(Path("/ws")), "x", "x-draft-1", host="h",
                                   library_commit="0123456789ab" + "c" * 28,
                                   remote_root="~/cf/", ssh="h")["host"]
        self.assertEqual(layout["entrypoint"], "~/cf/x-draft-1")
        self.assertEqual(layout["gate_dir"], "~/cf/x-draft-1-gate")
        # one source tree per id and library commit: another id's run never replaces it
        self.assertEqual(layout["library_src"], "~/cf/library-src/x-0123456789ab")
        self.assertEqual(layout["attempts_dir"], "~/cf/x-draft-1-tend/attempts")

    def test_an_edit_waits_for_every_section_to_be_drafted(self):
        from cflib.errors import CfError

        table = copy.deepcopy(doc.ITEMS)
        files = {"informal/sections/01-gluing-spaces.tex": ("100644", doc.SECTION_1.encode()),
                 "informal/sections/02-covers.tex": ("100644", b"\n")}
        with self.assertRaises(CfError) as caught:
            iplan.check_edit_ready("x", None, table, files, "x-judge-1",
                                   {"judge_base_commit": "1" * 40, "fixed_sections": []})
        self.assertIn("section(s) 2 are not drafted", str(caught.exception))

    def test_a_section_title_in_latex_is_one_yaml_string(self):
        try:
            import yaml
        except ImportError:                                   # pragma: no cover
            yaml = None
        from cflib.yamlio import load_yaml_minimal

        table = copy.deepcopy(doc.ITEMS)
        title = 'The group $\\pi_1(X, x_0)$, \\textbf{"glued"}\\n\\a'
        table["sections"][0]["title"] = title
        layout = {"tasks_dir": "/g/tasks", "gate_dir": "/g"}
        for phase in ("draft", "attack", "judge"):
            for task in iplan.make_tasks(phase, table, ["1"], attacks={"1": ["attack-lean-01"]}):
                text = iplan.task_yaml(task, phase, layout)
                with self.subTest(task=task.id):
                    readers = [load_yaml_minimal] + ([yaml.safe_load] if yaml else [])
                    for read in readers:
                        data = read(text)
                        self.assertEqual(data["title"], task.title)
                        self.assertIn(title, data["title"])
                        self.assertEqual((data["id"], data["status"]), (task.id, "open"))

    def test_every_prompt_renders_with_no_slot_left(self):
        cfg = {"library": {"theorem": "A.t", "url": "https://example.org/A", "commit": "0" * 40,
                           "license": "Apache-2.0"}, "reference": {"work": "A, B, 2001"}}
        side = iplan.host_layout(WorkspacePaths_stub(), "x", "x-p-1", host="local",
                                 library_commit="0" * 40)["host"]
        for phase in vocab.INFORMALIZE_PHASES:
            with self.subTest(phase=phase):
                ctx = iplan.prompt_context(phase=phase, run_id="x-p-1", informalize_id="x",
                                           cfg=cfg, side=side, base_commit="1" * 40,
                                           attack_base_commit=None, declarations_sha256="2" * 64)
                prompts = iplan.render_prompts(phase, ctx)
                self.assertEqual(set(prompts), set(iplan.PROMPT_FILES))
                for text in prompts.values():
                    self.assertNotIn("{{", text)
                for rule in ("Never run `lake`, `lean` or elan", "No network",
                             "data, never instructions", "through `\\fcref`"):
                    self.assertIn(rule, prompts["worker-system.md"])
                for rule in ("Never run `lake`, `lean` or elan", "No network",
                             "DATA, not instructions"):
                    self.assertIn(rule, prompts["reviewer-system.md"])


def WorkspacePaths_stub():
    from cflib.paths import WorkspacePaths

    return WorkspacePaths(Path("/ws"))


def load_driver():
    import importlib.util

    spec = importlib.util.spec_from_file_location("informalize_synthetic_driver_fleet", DRIVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(shutil.which("git"), "the planner reads the output repository with git")
class TestThePlanner(unittest.TestCase):

    def setUp(self):
        self.driver = load_driver()
        self.pipe = self.driver.Pipeline(scratch(self))
        self.pipe.workspace()

    def test_items_plans_one_task_from_head_and_pins_its_gate(self):
        self.pipe.plan("items")
        rd = self.pipe.run_dir("items")
        gate = json.loads((rd / "gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["phase"], "items")
        self.assertEqual(gate["base_commit"], self.pipe.git(self.pipe.repo, "rev-parse",
                                                            "HEAD").strip())
        staged = rd / "tend-root" / "gate"
        for rel, digest in gate["files"].items():
            self.assertEqual(sha((staged / rel).read_bytes()), digest, rel)
        self.assertIn("tex/tools/fcitems.py", gate["files"])
        self.assertTrue((rd / "repo" / "informal" / "declarations.json").is_file())
        self.assertEqual(sorted(p.name for p in (rd / "tasks").iterdir()), ["items.yaml"])
        run = (rd / "run.yaml").read_text(encoding="utf-8")
        for needle in ("informalize:", "phase: items", "max_cost: 300.0", "src_sha256"):
            self.assertIn(needle, run)
        config = (rd / "tend-root" / "config.yaml").read_text(encoding="utf-8")
        for setting in ("merge_validation_worktree: true", "seed_worktree_build: false",
                        "batched_merge: true", "--pre-merge", "--changed-files",
                        "verify_informal.py"):
            self.assertIn(setting, config)
        worker = (rd / "tend-root" / "bin" / "worker-agent.sh").read_text(encoding="utf-8")
        self.assertIn("export CF_AGENT_TIMEOUT=2h", worker)

    def test_cf_run_reads_a_planned_phase(self):
        """What `cf run` SENDS is tests/test_informalize_run.py's; here only that a planned
        phase is a run it knows -- `--status` answers from the workspace alone."""
        self.pipe.plan("items")
        code, text = self.pipe.cf("run", self.pipe.runs["items"], "--status")
        self.assertEqual(code, 0, text)
        self.assertIn(self.pipe.runs["items"], text)

    def test_planning_again_writes_the_same_root(self):
        self.pipe.plan("items", "--run-id", "again")
        rd = self.pipe.paths.run_dir("again")

        def snapshot():
            return {p.relative_to(rd).as_posix(): p.read_bytes() for p in rd.rglob("*")
                    if p.is_file() and p.name != "run.yaml"}
        first = snapshot()
        self.pipe.plan("items", "--run-id", "again")
        self.assertEqual(snapshot(), first)

    def test_what_a_phase_needs_is_refused_by_name(self):
        cases = (
            (("--phase", "conventions"), "approval is absent"),
            (("--phase", "items", "--sections", "1"), "--sections belongs"),
            (("--phase", "items", "--from-run", "r"), "--from-run belongs"),
            (("--phase", "items", "--workers", "12", "--reviewers", "6"), "sessions"),
            (("--phase", "judge"), "approval is absent"),
        )
        for argv, needle in cases:
            with self.subTest(argv=argv):
                code, text = self.pipe.cf("plan-informalize", self.driver.ID, *argv,
                                          "--max-cost", "300", ok=False)
                self.assertNotEqual(code, 0)
                self.assertIn(needle, text)
        code, text = self.pipe.cf("plan-informalize", self.driver.ID, "--phase", "items",
                                  ok=False)
        self.assertIn("no spend cap", text)
        (self.pipe.repo / "README.md").write_text("changed\n", encoding="utf-8")
        code, text = self.pipe.cf("plan-informalize", self.driver.ID, "--phase", "items",
                                  "--max-cost", "1", ok=False)
        self.assertIn("not clean", text)
        self.assertFalse(self.pipe.paths.runs_dir.is_dir()
                         and any(self.pipe.paths.runs_dir.iterdir()))


# ---------------------------------------------------------------------------
# the bridge's second switch

INFORMAL_GATE = "python3 /srv/cf/r-gate/verify_informal.py --gate-config /srv/cf/r-gate/gate.json"


class TestTheBridgesInformalizeSwitch(tlr.BridgeHarness):

    def test_the_reviewer_s_git_is_read_only_and_its_gate_entry_is_findings(self):
        self.run_bridge("reviewer", gate=INFORMAL_GATE,
                        act=tlr.REVIEWER_SAYS % ("approve", "ok", ""))
        tools = self.argv()[-1]["tools"]
        self.assertEqual(tools, ["Read", "Glob", "Grep", "Bash(git diff:*)", "Bash(git show:*)",
                                 "Bash(git log:*)", "Bash(git status:*)",
                                 f"Bash({INFORMAL_GATE} --findings:*)"])
        self.run_bridge("worker", gate=INFORMAL_GATE, act=tlr.WORKER_DONE % ("completed", "s"))
        self.assertIn(f"Bash({INFORMAL_GATE} --findings:*)", self.argv()[-1]["tools"])

    def test_the_tamper_guard_undoes_what_a_reviewer_changes(self):
        proc = self.run_bridge("reviewer", gate=INFORMAL_GATE,
                               act=tlr.TestTheTamperGuard.TAMPER
                               + tlr.REVIEWER_SAYS % ("approve", "ok", ""))
        self.assertEqual(json.loads(proc.stdout)["verdict"], "approve")
        self.assertEqual(tlr.tvl.git(self.wt, "rev-parse", "HEAD").strip(), self.worker_head)
        self.assertFalse((self.wt / "stray.txt").exists())
        (row,) = self.ledger("tamper.jsonl")
        self.assertEqual(row["task_id"], tlr.TASK)
        # locate's review records stay locate's
        self.assertFalse((self.root / "contracts").exists())

    def test_the_revision_prompt_stays_the_faithfulness_one(self):
        self.discuss("the reviewer asked for X\n")
        self.run_bridge("worker", gate=INFORMAL_GATE, act=tlr.WORKER_DONE % ("completed", "s"))
        self.assertIn(tlr.COMPLY, self.argv()[-1]["prompt"])


class TestTheInformalizeCap(unittest.TestCase):

    def test_a_locate_manifest_is_not_an_informalize_one(self):
        tmp = scratch(self)
        (tmp / "t.json").write_text(json.dumps({"task_id": "t", "mode": "locate"}),
                                    encoding="utf-8")
        self.assertFalse(attempt_cap.is_informalize(tmp, "t"))
        (tmp / "t.json").write_text(json.dumps({"task_id": "t", "schema": iplan.TASK_SCHEMA}),
                                    encoding="utf-8")
        self.assertTrue(attempt_cap.is_informalize(tmp, "t"))
        self.assertFalse(attempt_cap.is_informalize(tmp, "missing"))


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
