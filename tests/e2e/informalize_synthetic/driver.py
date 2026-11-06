#!/usr/bin/env python3
"""The informalize fleet on a fake library, with the gate run as the run host runs it.

New code, for docs/proposals/informalize.md sections 8.1 to 8.3 (component 5a of its
section 11).  On synthetic inputs and with nothing outside the checkout: a workspace with
an informalize id, a fake library source (`tests/_informalize_doc.py`'s `ExampleLib`, with
docstrings) and its `declarations.json`; then each phase planned with `cf
plan-informalize`, its contributions written by hand in worktrees of an entrypoint made as
`cf run` makes one (the planned `repo/` plus `tasks/`, committed, on `main`), and judged by
the STAGED gate as the host runs it: a copy of the planned `gate/` outside the root, its
config the run's `gate.local.json`, `python3 -I`, an environment of PATH and HOME alone and
the worktree as the working directory.  Good contributions pass; bad ones are refused under
the rule they break; the batched pre-merge check takes two drafts at once; a task at the
attempt cap is ended by `bin/attempt_cap.py` and admitted.  Between phases each run is
harvested by `cf harvest` from that entrypoint -- the documents to `corpus/<run>/<task>/`,
the files the run wrote to `corpus/<run>/tree/`, the gate re-run over both -- and applied
with `cf informalize apply` (component 5b): those files into the output repository,
committed, and the phase's stage receipt.  The maintainer's approvals run through
`cf informalize approve`.

The phases that build need pdflatex, latexmk and kpsewhich (`TEX`); without them the driver
stops after the items phase and says so.  Run it on its own with
``python3 tests/e2e/informalize_synthetic/driver.py [--keep <dir>]``; the suite runs it as
tests/test_e2e_informalize_synthetic.py.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent.parent
SKILL = TESTS.parent
for _entry in (str(TESTS), str(SKILL / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import _informalize_doc as doc  # noqa: E402
import cf as cf_main  # noqa: E402
from cflib.paths import WorkspacePaths  # noqa: E402
from informalize import apply as iapply  # noqa: E402
from informalize import stages  # noqa: E402

ID = "example-thm"
MODULE = "ExampleLib.Topology.Glue.Basic"
SOURCE_PATH = "ExampleLib/Topology/Glue/Basic.lean"
INIT = ["--library", doc.SLOTS["library_url"], "--commit", doc.COMMIT, "--module", MODULE,
        "--theorem", "ExampleLib.glue_theorem", "--prefix", "ExampleLib",
        "--text-license", "Apache-2.0", "--title", doc.SLOTS["title"],
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
#: a docstring above the glue theorem, whose words a draft must not copy (rule 9)
DOCSTRING = ("/-- The square formed by the intersection of two open sets, the two sets and the "
             "space they cover is a pushout of topological spaces in the category of spaces. -/")
COPIED = ("The square formed by the intersection of two open sets, the two sets and the space "
          "they cover is a pushout of topological spaces in the category of spaces.")
TEX = ("pdflatex", "latexmk", "kpsewhich")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}
#: the stages, in order; the ones after `items` build
STAGES = ("workspace", "items", "conventions", "draft", "attack", "judge", "edit", "cap",
          "broken")


class Failed(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


def have_tex() -> bool:
    """The three programs the phases that build need, and every package of `tex_packages`.

    The packages too: kpsewhich is on a machine whose TeX does not ship lmodern, and
    there the gate's build is exit 3 -- "the gate could not run" -- which is not a
    verdict on a contribution and not a failure of this driver either.  It is the list
    tests/test_informalize_runtime.py skips on.
    """
    if not all(shutil.which(p) for p in TEX):
        return False
    return all(subprocess.run(["kpsewhich", name], capture_output=True).returncode == 0
               for name in doc.TEX_PACKAGES)


class Pipeline:
    """One synthetic informalize id, phase by phase."""

    def __init__(self, root: Path):
        self.made = Path(root)
        self.ws = self.made / "ws"
        self.paths = WorkspacePaths(self.ws)
        self.repo = self.paths.informalize_repo(ID)
        self.runs: dict[str, str] = {}

    # -- small things -----------------------------------------------------------
    def cf(self, *argv: str, ok: bool = True) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cf_main.cli(["--workspace", str(self.ws), *argv])
            except SystemExit as exc:
                code = int(exc.code or 0)
        text = out.getvalue() + err.getvalue()
        if ok:
            check(code == 0, f"cf {' '.join(argv[:3])} failed ({code}):\n{text}")
        return code, text

    @staticmethod
    def git(cwd: Path, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                              env={**os.environ, **GIT_ENV})
        check(done.returncode == 0, f"git {' '.join(args)} failed in {cwd}: {done.stderr}")
        return done.stdout

    @staticmethod
    def write(path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, path: Path, data) -> Path:
        return self.write(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")

    # -- the workspace ----------------------------------------------------------
    def workspace(self) -> None:
        for name in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CF_ROLE", "TEND_TASK_ID"):
            os.environ.pop(name, None)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cf_main.cli(["init", str(self.ws), "--no-git", "-q"])
        check(code == 0, f"cf init failed: {out.getvalue()}{err.getvalue()}")
        self.cf("informalize", "init", ID, *INIT)
        lines = [f"-- line {n}" for n in range(1, 101)]
        for number, text in SOURCE_LINES.items():
            lines[number - 1] = text
        lines[38] = DOCSTRING
        self.write(self.paths.informalize_library_src(ID) / SOURCE_PATH, "\n".join(lines) + "\n")
        # what fetch-library (the commit's time) and scope (declarations.json) commit
        config = self.repo / "informal" / "informalize.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        self.write_json(config, dict(data, source_date_epoch=1700000000))
        self.write_json(self.repo / "informal" / "declarations.json", doc.DECLARATIONS)
        self.git(self.repo, "add", "informal/declarations.json", "informal/informalize.json")
        self.git(self.repo, "commit", "-qm", "scope")
        self.write_json(self.paths.informalize_scope(ID),
                        {"schema": "formal-companion/informalize-scope", "schema_version": 1,
                         "members": [{"name": d["name"]} for d in doc.DECLARATIONS["declarations"]]})

    # -- one run ----------------------------------------------------------------
    def plan(self, phase: str, *extra: str, ok: bool = True) -> tuple[int, str]:
        code, text = self.cf("plan-informalize", ID, "--phase", phase, "--max-cost", "300",
                             *extra, ok=ok)
        if code == 0:
            self.runs[phase] = text.split("planned informalize run ", 1)[1].split(":", 1)[0]
        return code, text

    def run_dir(self, phase: str) -> Path:
        return self.paths.run_dir(self.runs[phase])

    def host_gate(self, phase: str) -> Path:
        """The run's staged gate/, copied outside the root as `cf run` stages it, with the
        run's gate.local.json as its config (the "host" is this machine)."""
        copy = self.made / "host" / f"{self.runs[phase]}-gate"
        if not copy.is_dir():
            shutil.copytree(self.paths.tend_root(self.runs[phase]) / "gate", copy)
            shutil.copy2(self.run_dir(phase) / "gate.local.json", copy / "gate.json")
            data = json.loads((copy / "gate.json").read_text(encoding="utf-8"))
            # the copy's own attempts directory, where the cap stage plants a counter
            data["attempts_dir"] = str(self.made / "host" / f"{self.runs[phase]}-attempts")
            pins = data["files"]
            self.write_json(copy / "gate.json", data)
            check(set(pins) <= {p.relative_to(copy).as_posix()
                                for p in copy.rglob("*") if p.is_file()},
                  "gate.json pins a file the gate directory lacks")
        return copy

    def entrypoint(self, phase: str) -> Path:
        """The run's entrypoint as `cf run` makes it: repo/ plus tasks/, one commit on main."""
        ep = self.made / "host" / self.runs[phase]
        if not ep.is_dir():
            shutil.copytree(self.run_dir(phase) / "repo", ep)
            shutil.copytree(self.run_dir(phase) / "tasks", ep / "tasks")
            self.git(ep, "init", "-q", "-b", "main")
            self.git(ep, "add", "-A")
            self.git(ep, "commit", "-qm", f"tasks for {self.runs[phase]}")
        return ep

    def worktree(self, phase: str, task: str, name: str | None = None,
                 start: str = "main") -> Path:
        ep = self.entrypoint(phase)
        wt = self.made / "wt" / self.runs[phase] / (name or task)
        if wt.exists():
            self.git(ep, "worktree", "remove", "--force", str(wt))
            self.git(ep, "branch", "-D", name or task)
        self.git(ep, "worktree", "add", "-q", "-b", name or task, str(wt), start)
        return wt

    def complete(self, wt: Path, task: str, message: str = "done") -> None:
        path = wt / "tasks" / f"{task}.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("status: open",
                                                                 "status: complete"),
                        encoding="utf-8")
        self.git(wt, "add", "-A")
        self.git(wt, "commit", "-qm", message)

    def gate(self, phase: str, wt: Path, *argv: str, task: str = "") -> tuple[int, str]:
        """The STAGED gate, run as on the host."""
        gate = self.host_gate(phase)
        tools = sorted({str(Path(shutil.which(p)).parent) for p in ("git", "python3", *TEX)
                        if shutil.which(p)})
        env = {"PATH": ":".join(tools + ["/usr/bin", "/bin"]), "HOME": str(wt),
               "TEND_TASK_ID": task}
        done = subprocess.run([sys.executable, "-I", str(gate / "verify_informal.py"),
                               "--gate-config", str(gate / "gate.json"), "--root", str(wt),
                               *argv], cwd=str(wt), capture_output=True, text=True, env=env)
        return done.returncode, done.stdout + done.stderr

    def passes(self, phase: str, wt: Path, task: str = "", *argv: str) -> str:
        code, text = self.gate(phase, wt, *(argv or ("--changed-files",)), task=task)
        check(code == 0 and "VALIDATION FAILED" not in text,
              f"the gate refused a good {phase} contribution (exit {code}):\n{text}")
        return text

    def refused(self, phase: str, wt: Path, rule: int, what: str, *argv: str,
                task: str = "") -> str:
        code, text = self.gate(phase, wt, *(argv or ("--changed-files",)), task=task)
        check(code == 1 and "VALIDATION FAILED" in text and "could not run" not in text,
              f"the gate did not refuse {what} as a verdict (exit {code}):\n{text}")
        check(f"[{rule}]" in text, f"{what} was refused, but not under rule {rule}:\n{text}")
        return text

    def merge(self, phase: str, *branches: str) -> None:
        ep = self.entrypoint(phase)
        for branch in branches:
            self.git(ep, "merge", "-q", "--no-ff", "-m", f"merge {branch}", branch)

    def harvest(self, phase: str) -> None:
        """`cf harvest` on the entrypoint this machine plays the host with: the merged
        documents, the files the run wrote under `corpus/<run>/tree/`, the gate re-run
        over what landed and the receipt."""
        ep = self.entrypoint(phase)
        findings = ep / "findings"
        self.cf("harvest", self.runs[phase], "--from",
                str(findings if findings.is_dir() else ep))
        receipt = json.loads((self.paths.corpus_run_dir(self.runs[phase]) / "RECEIPT.json")
                             .read_text(encoding="utf-8"))
        check(receipt["complete"], f"the {phase} harvest is not complete: {receipt}")
        check((receipt.get("gate") or {}).get("ok"),
              f"the {phase} harvest's gate refused what landed: {(receipt.get('gate') or {})}")

    def apply(self, phase: str) -> None:
        """`cf informalize apply`: the files the harvest landed under `corpus/<run>/tree/`,
        committed in the output repository, and the phase's stage receipt.  `--unpriced`
        because this fleet is written by hand and no harvest here prices one."""
        run_id = self.runs[phase]
        corpus = self.paths.corpus_run_dir(run_id)
        receipt = json.loads((corpus / "RECEIPT.json").read_text(encoding="utf-8"))
        changed = list((receipt.get("informalize") or {}).get("changed") or [])
        before = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.cf("informalize", "apply", ID, "--run", run_id, "--unpriced")
        stage = iapply.stage_of(phase, run_id)
        written = stages.read_receipt(self.paths, ID, stage)
        check(written is not None, f"the {phase} apply wrote no {stage} receipt")
        check([e["run_id"] for e in written["runs"]] == [run_id],
              f"the {stage} receipt does not record {run_id}: {written['runs']}")
        check(sorted(written["outputs"]) == sorted(f"repo/{rel}" for rel in changed),
              f"the {stage} receipt's outputs are not the files the run wrote: {written}")
        after = self.git(self.repo, "rev-parse", "HEAD").strip()
        check((after != before) == bool(changed),
              f"the {phase} apply {'made no' if changed else 'made a'} commit")
        if changed:
            subject = self.git(self.repo, "log", "-1", "--format=%s").strip()
            check(subject == f"Informalize {phase} ({run_id}): {len(changed)} files",
                  f"the apply's commit message is {subject!r}")
        # it may be re-run: the second one finds its own commit and writes nothing
        self.cf("informalize", "apply", ID, "--run", run_id, "--unpriced")
        check(self.git(self.repo, "rev-parse", "HEAD").strip() == after,
              f"a second apply of {run_id} committed again")

    # -- the phases -------------------------------------------------------------
    def provisional_table(self) -> dict:
        import copy

        table = copy.deepcopy(doc.ITEMS)
        raw = (json.dumps(doc.DECLARATIONS, indent=1, ensure_ascii=False) + "\n").encode()
        table["library"]["declarations_sha256"] = hashlib.sha256(raw).hexdigest()
        renames = {"1.2": "1.4", "2.1": "2.2", "2.2": "2.5", "2.3": "2.7", "2.4": "2.9"}
        for item in table["items"]:
            item["id"] = renames.get(item["id"], item["id"])
        return table

    def items(self) -> None:
        code, text = self.plan("conventions", ok=False)
        check(code != 0 and "approval is absent" in text,
              f"the conventions phase was planned before the items approval:\n{text}")
        self.plan("items", "--workers", "1", "--reviewers", "1")
        wt = self.worktree("items", "items")
        self.write_json(wt / "informal" / "items.json", self.provisional_table())
        self.write(wt / "informal" / "items-review.md", "| id | kind | title |\n|---|---|---|\n")
        self.git(wt, "add", "-A")
        self.git(wt, "commit", "-qm", "draft the table")
        self.passes("items", wt, "", "--findings", "informal/items.json")
        # work left uncommitted: rule 1 (what merges is the commit)
        self.write(wt / "informal" / "items-review.md", "| id | kind | title |\n|---|---|---|\n"
                                                         "| 1.1 | definition | Gluing data |\n")
        self.refused("items", wt, 1, "an uncommitted change", task="items")
        self.git(wt, "checkout", "--", "informal/items-review.md")
        # a declaration's keyword line that is not the source's: rule 4
        table = self.provisional_table()
        table["items"][0]["declarations"][0]["line"] = 13
        self.write_json(wt / "informal" / "items.json", table)
        self.git(wt, "commit", "-qam", "wrong line")
        self.complete(wt, "items")
        self.refused("items", wt, 4, "a wrong keyword line")
        self.write_json(wt / "informal" / "items.json", self.provisional_table())
        self.git(wt, "commit", "-qam", "right line")
        # a path no task owns: rule 1, and nothing is built or checked after it
        self.write(wt / "README.md", "changed\n")
        self.git(wt, "commit", "-qam", "readme")
        self.refused("items", wt, 1, "a path outside the task's")
        self.git(wt, "reset", "-q", "--hard", "HEAD~1")
        # a contribution of no task's paths at all, with no task id: rule 1, not a pass
        stray = self.worktree("items", "items", "items-stray")
        self.write(stray / "README.md", "changed\n")
        self.git(stray, "commit", "-qam", "readme only")
        self.refused("items", stray, 1, "a change set no task owns")
        self.passes("items", wt)
        self.passes("items", wt, "items")
        self.merge("items", "items")
        self.harvest("items")
        self.apply("items")
        self.cf("informalize", "approve", ID, "items", "--by", "A. Maintainer",
                "--in-v1", "1.1,1.4,2.2,2.5,2.7")
        self.cf("informalize", "reference-checklist", ID)
        self.cf("informalize", "approve", ID, "references", "--by", "A. Maintainer")
        numbered = json.loads((self.repo / "informal" / "items.json").read_text(encoding="utf-8"))
        check([i["id"] for i in numbered["items"]] == ["1.1", "1.2", "2.1", "2.2", "2.3"],
              f"the approval did not number the table as the fixture: {numbered['items']}")

    def conventions(self) -> None:
        code, text = self.plan("draft", ok=False)
        check(code != 0 and "conventions phase has not been applied" in text,
              f"a draft was planned before the conventions:\n{text}")
        self.plan("conventions", "--workers", "1", "--reviewers", "1")
        wt = self.worktree("conventions", "conventions")
        informal = wt / "informal"
        self.write_json(informal / "hypotheses.json", doc.HYPOTHESES)
        notation = informal / "notation.sty"
        marker = "% Standing hypotheses (one \\fcdefhyp per key of hypotheses.json).\n"
        text = notation.read_text(encoding="utf-8")
        notation.write_text(text.replace(marker, marker + doc.NOTATION_HYPOTHESES), encoding="utf-8")
        conventions = informal / "sections" / "00-conventions.tex"
        self.write(conventions, conventions.read_text(encoding="utf-8") + doc.CONVENTIONS_NOTIONS)
        self.complete(wt, "conventions")
        self.passes("conventions", wt, "conventions")
        self.passes("conventions", wt, "", "--findings", "informal/hypotheses.json")
        # a hypothesis without its \fcdefhyp: rule 5
        bad = self.worktree("conventions", "conventions", "conventions-bad")
        self.write_json(bad / "informal" / "hypotheses.json",
                        {**doc.HYPOTHESES, "hypotheses": doc.HYPOTHESES["hypotheses"] + [
                            {"key": "closed", "macro": "\\hypClosed", "binder_head": "IsClosed"}]})
        self.complete(bad, "conventions")
        self.refused("conventions", bad, 5, "a hypothesis without its \\fcdefhyp", task="conventions")
        # a notion marked with a constant declarations.json does not hold: rule 2's
        # second lint, and a printed Lean name under the same rule
        for name, added, what in (
                ("notion", "\nA \\fcnotion{Mathlib.Imagined}{wish} is nowhere.\n",
                 "a notion no constant backs"),
                ("printed", "\nThe sets are \\texttt{IsOpen}.\n", "a printed Lean name")):
            bad = self.worktree("conventions", "conventions", f"conventions-{name}")
            self.write_json(bad / "informal" / "hypotheses.json", doc.HYPOTHESES)
            path = bad / "informal" / "sections" / "00-conventions.tex"
            self.write(path, path.read_text(encoding="utf-8") + added)
            self.complete(bad, "conventions")
            self.refused("conventions", bad, 2, what, task="conventions")
        # a forged record in notation.sty: rule 2's lint, before any build
        forged = self.worktree("conventions", "conventions", "conventions-forged")
        self.write_json(forged / "informal" / "hypotheses.json", doc.HYPOTHESES)
        self.write(forged / "informal" / "notation.sty",
                   notation.read_text(encoding="utf-8")
                   + "\\makeatletter\\immediate\\write\\@auxout{\\string\\fc@auxitem{9.9}}\n")
        self.complete(forged, "conventions")
        self.refused("conventions", forged, 2, "a forged .aux record", task="conventions")
        self.merge("conventions", "conventions")
        self.harvest("conventions")
        self.apply("conventions")

    def draft(self) -> None:
        self.plan("draft", "--sections", "all")
        wt1 = self.worktree("draft", "draft-01")
        self.write(wt1 / "informal/sections/01-gluing-spaces.tex", doc.SECTION_1)
        self.complete(wt1, "draft-01")
        # an ignored link the worktree holds and HEAD does not: what merges is HEAD, so it
        # is neither built nor refused
        os.symlink("/etc/hosts", wt1 / "informal" / "stray.pdf")
        self.passes("draft", wt1, "draft-01")
        wt2 = self.worktree("draft", "draft-02")
        self.write(wt2 / "informal/sections/02-covers.tex", doc.SECTION_2)
        self.complete(wt2, "draft-02")
        self.passes("draft", wt2, "draft-02")
        # the batched pre-merge check: both drafts at once, no task id
        pre = self.worktree("draft", "draft-01", "pre-merge")
        self.git(pre, "merge", "-q", "--no-ff", "-m", "batch", "draft-01", "draft-02")
        self.passes("draft", pre, "", "--changed-files", "--pre-merge")
        self.refused("draft", pre, 1, "two tasks' contributions without --pre-merge")
        # a key declaration not \leanref'd: rule 3, from check_items
        bad = self.worktree("draft", "draft-01", "draft-bad")
        self.write(bad / "informal/sections/01-gluing-spaces.tex",
                   doc.SECTION_1.replace("\\leanref{ExampleLib.GlueData}", ""))
        self.complete(bad, "draft-01")
        self.refused("draft", bad, 3, "a key declaration without its \\leanref", task="draft-01")
        # an undefined control sequence: rule 2, its message without TeX's input lines
        bad = self.worktree("draft", "draft-01", "draft-undefined")
        self.write(bad / "informal/sections/01-gluing-spaces.tex",
                   doc.SECTION_1.replace("Gluing data on", "\\nosuchmacro Gluing data on"))
        self.complete(bad, "draft-01")
        text = self.refused("draft", bad, 2, "an undefined control sequence", task="draft-01")
        check("l." not in text.split("VALIDATION FAILED", 1)[1].split("\n  - ")[0],
              f"the gate printed TeX's input lines:\n{text}")
        # a docstring's words copied: rule 9
        bad = self.worktree("draft", "draft-01", "draft-copied")
        self.write(bad / "informal/sections/01-gluing-spaces.tex",
                   doc.SECTION_1.replace("Let $U$ and $V$ be subsets", COPIED + " Let $U$ and "
                                                                        "$V$ be subsets"))
        self.complete(bad, "draft-01")
        self.refused("draft", bad, 9, "a docstring copied", task="draft-01")
        # a section that writes its own record: rule 2's lint, whatever the spelling; each
        # forgery stands in for the \leanref it drops, which check_items would refuse
        bad = self.worktree("draft", "draft-01", "draft-forged")
        self.write(bad / "informal/sections/01-gluing-spaces.tex",
                   doc.SECTION_1 + "\n\\makeatletter\\immediate\\write\\@auxout{x}\n")
        self.complete(bad, "draft-01")
        self.refused("draft", bad, 2, "a section that writes the .aux", task="draft-01")
        record = "{1.1}{}{ExampleLib.GlueData}"
        forgeries = {
            "carets": "\\makeat^^6cetter\\imm^^65diate\\wr^^69te\\^^40auxout{\\string"
                      "\\fc^^40auxleanref" + record + "}",
            "expl3": "\\ExplSyntaxOn\\iow_now:cx{ @auxout }{\\token_to_str:c{fc @auxleanref}"
                     + record + "}\\ExplSyntaxOff",
            "verb": "\\verb|%|\\makeatletter\\immediate\\write\\@auxout{\\string\\fc@auxleanref"
                    + record + "}\\makeatother\n",
            "url": "\\url{%}\\makeatletter\\immediate\\write\\@auxout{\\string\\fc@auxleanref"
                   + record + "}\\makeatother\n",
            "label": "\\label{\\string\\fc @auxleanref" + record + "}",
        }
        dropped = doc.SECTION_1.replace("\\leanref{ExampleLib.GlueData}", "")
        for name, forgery in forgeries.items():
            bad = self.worktree("draft", "draft-01", f"draft-forged-{name}")
            self.write(bad / "informal/sections/01-gluing-spaces.tex",
                       dropped.replace("\\end{definition}", forgery + "\\end{definition}", 1))
            self.complete(bad, "draft-01")
            self.refused("draft", bad, 2, f"a record forged with {name}", task="draft-01")
        # a label another section may also define: rule 2 (the whole build would refuse it)
        bad = self.worktree("draft", "draft-01", "draft-label")
        self.write(bad / "informal/sections/01-gluing-spaces.tex", doc.SECTION_1.replace(
            "\\end{definition}", "\\begin{equation}\\label{eq:main}x\\end{equation}"
                                 "\\end{definition}", 1))
        self.complete(bad, "draft-01")
        self.refused("draft", bad, 2, "a label of no section's form", task="draft-01")
        # draft-01 changing section 2's file: rule 1
        bad = self.worktree("draft", "draft-01", "draft-trespass")
        self.write(bad / "informal/sections/01-gluing-spaces.tex", doc.SECTION_1)
        self.write(bad / "informal/sections/02-covers.tex", doc.SECTION_2)
        self.complete(bad, "draft-01")
        self.refused("draft", bad, 1, "another section's file", task="draft-01")
        self.merge("draft", "draft-01", "draft-02")
        self.harvest("draft")
        self.apply("draft")
        code, text = self.plan("draft", "--sections", "1", ok=False)
        check(code != 0 and "drafted already" in text, f"a second draft was planned:\n{text}")

    def attack_document(self, task: str, direction: str, section: str, items: list[str],
                        findings: list[dict]) -> dict:
        return {"schema": "formal-companion/informalize-attack", "schema_version": 1,
                "task": task, "section": section, "direction": direction,
                "base_commit": self.base("attack"), "checked_items": items, "findings": findings}

    def base(self, phase: str) -> str:
        gate = json.loads((self.run_dir(phase) / "gate.local.json").read_text(encoding="utf-8"))
        return gate["base_commit"]

    def attack(self) -> None:
        self.plan("attack")
        finding = {"item": "1.2", "part": "b", "severity": "imprecise",
                   "quote": "the map induced by gluing is surjective",
                   "declarations": ["ExampleLib.glue_surjective"],
                   "what": "The text does not say that U and V are open in part (b).",
                   "lean_evidence": "glue_surjective binds hU : IsOpen U and hV : IsOpen V.",
                   "fix": "Name both hypotheses in part (b)."}
        style = {"item": "1.1", "part": None, "severity": "style",
                 "quote": "is two open subsets of $X$",
                 "declarations": ["ExampleLib.GlueData"],
                 "what": "The text could name the space before the subsets.",
                 "lean_evidence": "GlueData takes X before the two sets.",
                 "fix": "Say that X is a topological space first."}
        docs = {"attack-lean-01": ("lean-first", "1", ["1.1", "1.2"], [finding, style]),
                "attack-tex-01": ("tex-first", "1", ["1.1", "1.2"], []),
                "attack-lean-02": ("lean-first", "2", ["2.1", "2.2", "2.3"], []),
                "attack-tex-02": ("tex-first", "2", ["2.1", "2.2", "2.3"], [])}
        for task, (direction, section, items, findings) in docs.items():
            wt = self.worktree("attack", task)
            self.write_json(wt / "findings" / task / "attack.json",
                            self.attack_document(task, direction, section, items, findings))
            self.complete(wt, task)
            self.passes("attack", wt, task)
            self.passes("attack", wt, "", "--findings", f"findings/{task}/attack.json")
        # a quote that is not the text's: rule 6
        bad = self.worktree("attack", "attack-lean-01", "attack-misquote")
        self.write_json(bad / "findings/attack-lean-01/attack.json", self.attack_document(
            "attack-lean-01", "lean-first", "1", ["1.1", "1.2"],
            [dict(finding, quote="the induced map is onto")]))
        self.complete(bad, "attack-lean-01")
        self.refused("attack", bad, 6, "a quote not in the section", task="attack-lean-01")
        # an attacker that edits the text: rule 1
        bad = self.worktree("attack", "attack-tex-01", "attack-edits")
        self.write_json(bad / "findings/attack-tex-01/attack.json", self.attack_document(
            "attack-tex-01", "tex-first", "1", ["1.1", "1.2"], []))
        path = bad / "informal/sections/01-gluing-spaces.tex"
        self.write(path, path.read_text(encoding="utf-8") + "% an edit\n")
        self.complete(bad, "attack-tex-01")
        self.refused("attack", bad, 1, "an attacker that edits the section", task="attack-tex-01")
        self.merge("attack", *docs)
        self.harvest("attack")
        # the harvest's re-check at home: no worktree, --root the run's planned repo/ (the
        # base commit's informal/), each document where the harvest landed it
        corpus = self.paths.corpus_run_dir(self.runs["attack"])
        self.passes("attack", self.run_dir("attack") / "repo", "",
                    *[a for task in docs for a in ("--findings",
                                                    str(corpus / task / "attack.json"))])
        # an attack run changes nothing under informal/: its apply writes only the receipt
        self.apply("attack")

    def judge(self) -> None:
        # a section that moved since the attack run: its findings quote another text
        path = self.repo / "informal/sections/01-gluing-spaces.tex"
        self.write(path, path.read_text(encoding="utf-8") + "\nOne more sentence.\n")
        self.git(self.repo, "commit", "-qam", "a change after the attack run")
        code, text = self.plan("judge", "--from-run", self.runs["attack"], ok=False)
        check(code != 0 and "differs from the attack run's base commit" in text,
              f"a judge run was planned on a section that moved:\n{text}")
        self.git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        self.plan("judge", "--from-run", self.runs["attack"])
        planned = json.loads((self.run_dir("judge") / "gate.local.json").read_text(encoding="utf-8"))
        check(planned["attack_base_commit"] == self.base("attack"),
              "the judge run does not carry its attack run's base commit")
        tasks = sorted(p.stem for p in (self.run_dir("judge") / "tasks").glob("*.yaml"))
        check(tasks == ["judge-01"], f"judge tasks are for the sections with findings: {tasks}")
        wt = self.worktree("judge", "judge-01")
        path = wt / "informal/sections/01-gluing-spaces.tex"
        self.write(path, path.read_text(encoding="utf-8").replace(
            "the map induced by gluing is surjective",
            "the map induced by gluing is surjective, $U$ and $V$ being open"))
        ruling = {"schema": "formal-companion/informalize-ruling", "schema_version": 1,
                  "task": "judge-01", "section": "1", "base_commit": self.base("attack"),
                  "rulings": [{"finding": "attack-lean-01#0", "ruling": "real", "action": "fixed",
                               "reason": "glue_surjective binds both openness hypotheses."},
                              {"finding": "attack-lean-01#1", "ruling": "real",
                               "action": "out_of_scope",
                               "reason": "The order of objects is fixed in the conventions."}],
                  "requests": [{"finding": "attack-lean-01#1",
                                "file": "informal/sections/00-conventions.tex",
                                "what": "Introduce X before its subsets.",
                                "why": "GlueData takes X first."}]}
        self.write_json(wt / "findings/judge-01/ruling.json", ruling)
        self.complete(wt, "judge-01")
        self.passes("judge", wt, "judge-01")
        self.passes("judge", wt, "", "--findings", "findings/judge-01/ruling.json")
        # a finding left without a ruling: rule 7
        bad = self.worktree("judge", "judge-01", "judge-missing")
        self.write_json(bad / "findings/judge-01/ruling.json",
                        dict(ruling, rulings=ruling["rulings"][:1], requests=[]))
        self.complete(bad, "judge-01")
        self.refused("judge", bad, 7, "a finding without a ruling", task="judge-01")
        # "fixed" with the section unchanged: rule 7
        bad = self.worktree("judge", "judge-01", "judge-unfixed")
        self.write_json(bad / "findings/judge-01/ruling.json", ruling)
        self.complete(bad, "judge-01")
        self.refused("judge", bad, 7, "fixed without a change", task="judge-01")
        self.merge("judge", "judge-01")
        self.harvest("judge")
        code, text = self.plan("edit", "--from-run", self.runs["judge"], ok=False)
        check(code != 0 and "is not applied" in text,
              f"an edit run was planned before its judge run was applied:\n{text}")
        self.apply("judge")
        # the judge's fix touched section 1 alone, so the next attack run covers it alone
        code, text = self.cf("plan-informalize", ID, "--phase", "attack", "--sections",
                             "touched", "--max-cost", "300", "--run-id", "touched-check")
        tasks = sorted(p.stem for p in self.paths.tasks_dir("touched-check").glob("*.yaml"))
        check(tasks == ["attack-lean-01", "attack-tex-01"],
              f"--sections touched chose {tasks}, not section 1's attackers")

    def edit(self) -> None:
        self.plan("edit", "--from-run", self.runs["judge"])
        wt = self.worktree("edit", "edit")
        path = wt / "informal/sections/00-conventions.tex"
        self.write(path, path.read_text(encoding="utf-8")
                   + "\nThroughout, $X$ is a topological space, named before its subsets.\n")
        self.write_json(wt / "findings/edit/edit.json",
                        {"task": "edit", "answers": [{"finding": "attack-lean-01#1",
                                                      "status": "done",
                                                      "reason": "Added to the conventions."}]})
        self.write_json(wt / "findings/edit/items-proposal.json",
                        {"task": "edit", "changes": [{"item": "1.1", "field": "title",
                                                      "from": "Gluing data",
                                                      "to": "Gluing data on a space",
                                                      "why": "The title names the space."}]})
        self.complete(wt, "edit")
        self.passes("edit", wt, "edit")
        # the table changed in a run: rule 1 (items.json is no task's)
        bad = self.worktree("edit", "edit", "edit-table")
        table = bad / "informal/items.json"
        self.write(table, table.read_text(encoding="utf-8").replace("Gluing data\"",
                                                                    "Gluing data on a space\""))
        self.write_json(bad / "findings/edit/edit.json",
                        {"task": "edit", "answers": [{"finding": "attack-lean-01#1",
                                                      "status": "declined", "reason": "x y z"}]})
        self.complete(bad, "edit")
        self.refused("edit", bad, 1, "a change to items.json", task="edit")
        # a request left unanswered: rule 8
        bad = self.worktree("edit", "edit", "edit-silent")
        self.write_json(bad / "findings/edit/edit.json", {"task": "edit", "answers": []})
        self.complete(bad, "edit")
        self.refused("edit", bad, 8, "an unanswered request", task="edit")
        self.merge("edit", "edit")
        self.harvest("edit")
        self.apply("edit")

    def cap(self) -> None:
        """A task at the attempt cap: the bridge's helper ends it, and the gate admits it."""
        phase = "draft" if "draft" in self.runs else "items"
        task = "draft-01" if phase == "draft" else "items"
        # from the entrypoint's first commit, where the task is still open
        first = self.git(self.entrypoint(phase), "rev-list", "--max-parents=0", "HEAD").split()[0]
        # below the cap, the task file's status alone is no contribution: rule 1
        alone = self.worktree(phase, task, "status-only", start=first)
        self.complete(alone, task)
        self.refused(phase, alone, 1, "a task file alone below the cap", task=task)
        wt = self.worktree(phase, task, "capped", start=first)
        self.write(wt / "README.md", "an attempt that strayed\n")
        self.git(wt, "commit", "-qam", "stray")
        attempts = self.made / "host" / f"{self.runs[phase]}-attempts"
        self.refused(phase, wt, 1, "a stray path below the cap", task=task)
        self.write_json(attempts / f"{task}.json", {"rejections": 3})
        cap = SKILL / "scripts" / "run" / "attempt_cap.py"
        manifests = self.host_gate(phase) / "tasks"
        for role in ("worker", "reviewer"):
            done = subprocess.run([sys.executable, str(cap), role, "--root", str(self.made / "x"),
                                   "--task", task, "--manifests", str(manifests),
                                   "--worktree", str(wt), "--cap", "3"],
                                  capture_output=True, text=True, cwd=str(wt),
                                  env={**os.environ, **GIT_ENV})
            check(done.returncode == 10, f"the cap fired with no counter ({role}): {done.stderr}")
        root = self.made / "x"
        self.write_json(root / "attempts" / f"{task}.json", {"rejections": 3})
        for role in ("worker", "reviewer"):
            done = subprocess.run([sys.executable, str(cap), role, "--root", str(root),
                                   "--task", task, "--manifests", str(manifests),
                                   "--worktree", str(wt), "--cap", "3"],
                                  capture_output=True, text=True, cwd=str(wt),
                                  env={**os.environ, **GIT_ENV})
            check(done.returncode == 0, f"attempt_cap {role} did not end the task: "
                                        f"{done.stdout}{done.stderr}")
        check(not (wt / "README.md").read_text(encoding="utf-8").startswith("an attempt"),
              "the cap left the stray change")
        text = self.passes(phase, wt, task)
        check("at the attempt cap" in text, f"the gate did not say the task is capped:\n{text}")

    def broken(self) -> None:
        """A pinned file that moved is the gate's own failure (exit 3), never a verdict."""
        phase = "items"
        gate = self.host_gate(phase)
        code, text = self.gate(phase, self.made, "--self-check")
        check(code == 0 and "OK: gate self-check" in text, f"the self-check failed:\n{text}")
        (gate / "declarations.json").write_text("{}\n", encoding="utf-8")
        code, text = self.gate(phase, self.made, "--changed-files")
        check(code == 3 and "could not run" in text and "not the file the plan pinned" in text,
              f"a moved pinned file did not stop the gate (exit {code}):\n{text}")
        code, text = self.gate(phase, self.made, "--findings", "x.json", "--changed-files")
        check(code == 3 and "refused" in text, f"--findings beside --changed-files ran:\n{text}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", type=Path, default=None,
                    help="build in this directory and leave it (default: a temporary one)")
    args = ap.parse_args(argv)
    root = args.keep or Path(tempfile.mkdtemp(prefix="cf-informalize-e2e-"))
    root.mkdir(parents=True, exist_ok=True)
    pipe = Pipeline(root)
    names = STAGES if have_tex() else ("workspace", "items", "cap", "broken")
    if not have_tex():
        print("no TeX here: the phases that build are skipped", file=sys.stderr)
    try:
        for name in names:
            getattr(pipe, name)()
            print(f"ok {name}")
    finally:
        if args.keep is None:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
