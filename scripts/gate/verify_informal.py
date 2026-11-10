#!/usr/bin/env python3
"""The informalize gate: what a fleet task's contribution must pass before it merges.

New code, for docs/proposals/informalize.md section 8.3 (component 5a of its section 11),
on the pattern of the locate gate (`verify_locate.py`): it runs out of tree, from the
planned `gate/` directory of a tend root, with `python3 -I` and nothing but the standard
library, its sibling `verify_findings.py` (the hardened change-set reader and tend's
orchestrator lookup) and `tex/`, the informalize runtime as the plan staged it, whose
`tools/` it imports (`fccommon.py`, `fcitems.py`) and runs (`build_pdf.sh`,
`check_items.py`).  `gate.json` beside it names the run, its phase and base commit, the
library's source on the host, and pins the sha256 of every file the gate reads from its
directory: a file that moved is exit 3, never a verdict.

The rules, each refusal naming its number:

1. **Changed files** -- only the task's owned paths (its manifest's `paths`) and its task
   file's status line; nothing deleted; the task's outputs written (a task at the attempt
   cap may change its task file alone).  The task id is `--task-id`, `$TEND_TASK_ID`,
   tend's `orchestrator.sqlite`, else the one task the change set belongs to; only
   `--pre-merge` admits several.  A refusal here builds nothing.
2. **Build** -- `informal/` at the contribution's HEAD, read blob by blob with the pinned
   git (no filter runs, a link is refused), built in a scratch directory with the gate's
   own `build_pdf.sh` (`--only <section>` for draft and judge, the whole document for
   conventions and edit) under a time limit, in a process group of its own in the gate's
   session, stopped as a group at the limit or when the gate is stopped; and a lint of the
   text files, comments included: no `\\makeatletter`, control sequence holding `@`, `^^`,
   expl3, `\\write`, `\\openout`, `\\def`, `\\csname`, `\\string`, `\\input` or their kin,
   with which a section could forge the records the checks read, a `\\label` key only
   of its own section's form, and, of what the file PRINTS, no monospace font, link, URL
   or name of `declarations.json` -- the text names no Lean declaration.
3. **Aux checks** -- the gate's `check_items.py --aux-only --strict` on that build, against
   the gate's `declarations.json` (`--conventions` in the conventions phase).
4. **Items** -- `items.json` keeps section 5.4's rules and passes `check-items` against the
   gate's `declarations.json` and the library's source (`fcitems.check_table`); the review
   file is written.
5. **Conventions** -- `hypotheses.json` keeps section 5.5's rules and every key has its
   `\\fcdefhyp` in `notation.sty`.
6. **Attack** -- the document's shape; its `base_commit` is the run's; every finding's item
   and part are the section's; its `quote` occurs verbatim in the section file; every
   declaration it names is in `declarations.json`; `checked_items` is every `in_v1` item
   of the section.
7. **Judge** -- every finding of the section's attack documents has one ruling; the
   ruling's `base_commit` is its attack run's; `fixed` only when the section file changed;
   `out_of_scope` only with a request, and a request only for such a finding.
8. **Edit** -- every finding the judge run made requests for answered once, `done` or
   `declined` with a reason (one answer covers all of a finding's requests); a changed
   section file is one a request names; `items-proposal.json`, when present, keeps its
   shape.
9. **Copying** -- no run of `copy_run_words` words of a text file the task wrote occurs in
   a docstring or comment of the library's source files of the items it covers: the one
   way the reference work's prose, which the library's docstrings sometimes quote, can be
   detected here.

`--findings DOC` checks a task's own JSON documents alone (rules 6 to 8 without the
change set or the build), with `informal/` read from `--root`: the reviewer's call in the
worktree, and the harvest's at home with `--root` at the run's planned `repo/` (the base
commit's files) and each document where the harvest landed it.  `--self-check`
is the verify stage's: the imports, the pins, the task manifests, the library's source
and, for a phase that builds, TeX and every package of `tex_packages`.

Exit status: 0 passed; 1 REJECTED, with a `VALIDATION FAILED` list -- fix the
contribution; 3 the gate itself could not run (a missing or moved file, an unreadable
config, a refused command line, a missing program), which is never a finding of the
worker's.  The error messages of a build are printed alone, never the input lines TeX
shows around them.  Every call appends one line to the gate log (`log_dir`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

sys.dont_write_bytecode = True

HERE = pathlib.Path(__file__).resolve().parent
#: the runtime: beside this file in a gate directory, else the checkout's templates
TEX = HERE / "tex" if (HERE / "tex").is_dir() else \
    HERE.parent.parent / "templates" / "informalize" / "runtime"
for _entry in (str(TEX / "tools"), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

#: what went wrong importing the gate's siblings: exit 3 with one line, never a traceback
IMPORT_ERRORS: list[str] = []
try:
    import verify_findings as vf  # noqa: E402
except Exception as _exc:  # noqa: BLE001 - any failure to import is the same failure
    vf = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"verify_findings.py ({_exc.__class__.__name__}: {_exc})")
try:
    import fccommon as fc  # noqa: E402
    import fcitems as fi  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    fc = fi = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"tex/tools/fccommon.py or fcitems.py ({_exc.__class__.__name__}: {_exc})")

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_GATE_BROKEN = 3

GATE_SCHEMA = "cf-gate-informalize/1"
TASK_SCHEMA = "cf-informalize-task/1"
ATTACK_SCHEMA = "formal-companion/informalize-attack"
RULING_SCHEMA = "formal-companion/informalize-ruling"
SCHEMA_VERSION = 1
#: COPIED from cflib.vocab (no cflib on the host); tests/test_informalize_fleet.py pins them
PHASES = ("items", "conventions", "draft", "attack", "judge", "edit")
SEVERITIES = ("wrong", "imprecise", "style")
DIRECTIONS = ("lean-first", "tex-first")
RULINGS = ("real", "not_real")
ACTIONS = ("fixed", "out_of_scope", "none")
ANSWERS = ("done", "declined")
ATTEMPT_CAP = 3
BUILD_TIMEOUT = 600
COPY_RUN_WORDS = 12
#: the conventions task's files, which every section reads (section 8.2)
CONVENTION_FILES = ("informal/notation.sty", "informal/sections/00-conventions.tex",
                    "informal/STYLE-project.md", "informal/hypotheses.json")
#: the phases whose tasks build the document, and how
SECTION_BUILDS = ("draft", "judge")
WHOLE_BUILDS = ("conventions", "edit")
#: what the gate reads of a contribution's tree
TREE_PREFIXES = ("informal/", "findings/")
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_TASK_FILE_BYTES = 1024 * 1024
MAX_PROBLEMS = 200
MAX_TEXT_CHARS = 2000
#: seconds the build script gets to stop latexmk and remove its lock after SIGTERM
STOP_GRACE = 10

#: rule 2's lint of a text file, comments included (a `\verb`, a `\url` or a verbatim
#: environment makes a `%` text, and TeX runs what follows it): control sequences with which
#: a section could write an .aux record itself, open a file, spell a name or a backslash the
#: lint cannot see, or redefine what the records are written by.  It is a list of what is
#: refused, not of what is allowed; the reviewer reads the rest.
LINT = (
    (re.compile(r"\\makeatletter(?![A-Za-z])"), "\\makeatletter"),
    (re.compile(r"\\[A-Za-z]*@[A-Za-z@]*"), "a control sequence holding @"),
    (re.compile(r"\^\^"), "TeX's ^^ character notation"),
    (re.compile(r"\\(?:ExplSyntaxOn|ProvidesExpl(?:Package|Class|File))(?![A-Za-z])"),
     "expl3 syntax"),
    (re.compile(r"\\(?:immediate|write|openout|closeout|openin|read|newwrite|newread)"
                r"(?![A-Za-z])"), "a file operation"),
    (re.compile(r"\\[egx]?def(?![A-Za-z])"), "\\def"),
    (re.compile(r"\\(?:let|futurelet|renewcommand|renewenvironment|DeclareRobustCommand"
                r"|(?:Renew|Declare)Document(?:Command|Environment)"
                r"|(?:New|Renew|Declare)(?:Command|Environment)Copy"
                r"|AddToHook(?:Next)?|AtBeginDocument|AtEndDocument"
                r"|(?:patch|preto|appto)cmd|cs(?:use|[egx]?def|let(?:cs)?)|letcs"
                r"|catcode|uccode|lccode|newlinechar|endlinechar|escapechar"
                r"|csname|ifcsname|UseName|ExpandArgs|expandafter|scantokens"
                r"|string|detokenize|unexpanded|meaning)(?![A-Za-z])"),
     "a redefinition, a hook, a catcode, or a name or string built from text"),
    (re.compile(r"\\(?:input|include|InputIfFileExists|IfFileExists|openin)(?![A-Za-z])"),
     "an \\input"),
    (re.compile(r"\\(?:special|pdf[A-Za-z]*|zsavepos)(?![A-Za-z])"),
     "a raw PDF or position command"),
)
#: rule 2's second lint, on a text file with its comments stripped: what a section may not
#: PRINT.  The informal text never prints a Lean declaration name or a link to the
#: library's documentation -- Literature Links supplies both, from the markers -- so a
#: notion the conventions fix is written as plain mathematics inside `\fcnotion`, whose
#: first argument carries the constant and prints nothing.
PRINTED_LINT = (
    (re.compile(r"\\(?:texttt|mathtt|ttfamily|tt)(?![A-Za-z])"), "a monospace font"),
    (re.compile(r"\\(?:href|url|nolinkurl|path)(?![A-Za-z])"), "a link"),
    (re.compile(r"\\verb(?![A-Za-z*])"), "\\verb"),
    (re.compile(r"https?://"), "a URL"),
)
#: a `\label` key a text file may write: letters, digits and `:._-`; in a numbered section's
#: file `eq:<id>-<word>` of one of its items (STYLE.md), so no two sections share a label
LABEL = re.compile(r"\\label(?![A-Za-z])\s*(?:\{([A-Za-z0-9][A-Za-z0-9:._-]*)\})?")
NUMBERED_LABEL = re.compile(r"(?:eq|sec):[0-9]|item:")


class GateBroken(Exception):
    """The gate cannot run: an infrastructure failure, never a verdict on the contribution."""


class Problems:
    """Refusals, each under its rule number."""

    def __init__(self) -> None:
        self._items: list[tuple[int, str, str]] = []

    def add(self, rule: int, where: str, message: str) -> None:
        if len(self._items) < MAX_PROBLEMS:
            self._items.append((rule, str(where), str(message)))

    def extend(self, other: "Problems") -> None:
        for item in other._items:
            self.add(*item)

    def __len__(self) -> int:
        return len(self._items)

    def lines(self) -> list[str]:
        return [f"[{rule}] {where}: {message}" for rule, where, message in self._items]

    def by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rule, _where, _message in self._items:
            out[str(rule)] = out.get(str(rule), 0) + 1
        return dict(sorted(out.items()))


# --------------------------------------------------------------------------- #
# the configuration
# --------------------------------------------------------------------------- #

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(base: pathlib.Path, relative: Any) -> pathlib.Path | None:
    """``base / relative`` for a plain relative path that stays in ``base``, else None."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    pure = pathlib.PurePosixPath(relative)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        return None
    return base.joinpath(*pure.parts)


def _optional_path(value: Any, key: str) -> pathlib.Path | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise GateBroken(f"gate.json {key} is not a path")
    return pathlib.Path(value).expanduser()


def _int(data: dict, key: str, default: int, low: int = 1) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise GateBroken(f"gate.json {key} {value!r} is not a whole number >= {low}")
    return value


class GateConfig:
    """`gate.json`, checked, and every file it pins checked against its sha256."""

    def __init__(self, path: pathlib.Path, data: dict):
        self.path = path
        self.home = path.parent
        if data.get("schema") != GATE_SCHEMA:
            raise GateBroken(f"{path} is not an informalize gate config ({GATE_SCHEMA})")
        self.id = str(data.get("id") or "")
        self.run_id = str(data.get("run_id") or "")
        self.phase = data.get("phase")
        if self.phase not in PHASES:
            raise GateBroken(f"gate.json phase {self.phase!r} is not one of {', '.join(PHASES)}")
        self.base_commit = str(data.get("base_commit") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", self.base_commit):
            raise GateBroken("gate.json base_commit is not 40 hex")
        self.attack_base_commit = str(data.get("attack_base_commit") or "")
        library = data.get("library") if isinstance(data.get("library"), dict) else {}
        self.library_commit = str(library.get("commit") or "")
        self.library_src = _optional_path(library.get("src"), "library.src")
        self.base_files = data.get("base_files") if isinstance(data.get("base_files"), dict) else {}
        self.tasks_dir = self.home / "tasks"
        self.data_dir = self.home / "data"
        self.log_dir = _optional_path(data.get("log_dir"), "log_dir")
        self.orchestrator_db = _optional_path(data.get("orchestrator_db"), "orchestrator_db")
        self.attempts_dir = _optional_path(data.get("attempts_dir"), "attempts_dir")
        self.attempt_cap = _int(data, "attempt_cap", ATTEMPT_CAP)
        self.build_timeout = _int(data, "build_timeout", BUILD_TIMEOUT)
        self.copy_run_words = _int(data, "copy_run_words", COPY_RUN_WORDS, low=4)
        packages = data.get("tex_packages") or []
        if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
            raise GateBroken("gate.json tex_packages is not a list of file names")
        self.tex_packages = list(packages)
        files = data.get("files")
        if not isinstance(files, dict) or not files:
            raise GateBroken("gate.json pins no file (files)")
        self.files = files
        self.declarations: dict[str, dict] = {}
        self.references: dict[str, dict] = {}
        self.declarations_sha256 = ""

    @classmethod
    def load(cls, path: pathlib.Path) -> "GateConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise GateBroken(f"gate config {path} is unreadable ({exc})") from None
        if not isinstance(data, dict):
            raise GateBroken(f"gate config {path} is not an object")
        cfg = cls(path, data)
        cfg.verify_pins()
        cfg.load_declarations()
        return cfg

    def verify_pins(self) -> None:
        """Every pinned file is there with its sha256, and every manifest is pinned."""
        for rel, want in sorted(self.files.items()):
            path = _inside(self.home, rel)
            if path is None:
                raise GateBroken(f"gate.json pins {rel!r}, which is not a path in the gate directory")
            try:
                if not stat.S_ISREG(os.lstat(path).st_mode):
                    raise GateBroken(f"{path} is not a regular file")
                got = _sha256_file(path)
            except OSError as exc:
                raise GateBroken(f"the pinned file {path} is unreadable ({exc})") from None
            if got != want:
                raise GateBroken(f"{path} is not the file the plan pinned (sha256 {got[:12]}, "
                                 f"not {str(want)[:12]}): re-plan, or restore the gate directory")
        for manifest in sorted(self.tasks_dir.glob("*.json")) if self.tasks_dir.is_dir() else []:
            if f"tasks/{manifest.name}" not in self.files:
                raise GateBroken(f"{manifest} is a task manifest the plan did not pin")

    def load_declarations(self) -> None:
        path = self.home / "declarations.json"
        if "declarations.json" not in self.files:
            raise GateBroken("gate.json pins no declarations.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("declarations") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise GateBroken(f"{path} holds no declarations list")
        self.declarations = {r["name"]: r for r in rows
                             if isinstance(r, dict) and isinstance(r.get("name"), str)}
        refs = data.get("references") if isinstance(data, dict) else None
        self.references = {r["name"]: r for r in refs if isinstance(r, dict)
                           and isinstance(r.get("name"), str)} if isinstance(refs, list) else {}
        self.declarations_sha256 = self.files["declarations.json"]

    def manifest(self, task_id: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", task_id or ""):
            return None
        path = self.tasks_dir / f"{task_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateBroken(f"the task manifest {path} is unreadable ({exc})") from None
        if not isinstance(data, dict) or data.get("task_id") != task_id \
                or data.get("schema") != TASK_SCHEMA:
            raise GateBroken(f"{path} is not task {task_id}'s manifest")
        return data

    def manifests(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for path in sorted(self.tasks_dir.glob("*.json")) if self.tasks_dir.is_dir() else []:
            found = self.manifest(path.stem)
            if found is not None:
                out[path.stem] = found
        return out

    def data_document(self, kind: str, name: str) -> dict:
        """A staged document of the run this phase reads (`data/attacks/`, `data/rulings/`)."""
        rel = f"data/{kind}/{name}.json"
        if rel not in self.files:
            raise GateBroken(f"gate.json pins no {rel}")
        try:
            data = json.loads((self.home / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateBroken(f"{rel} is unreadable ({exc})") from None
        if not isinstance(data, dict):
            raise GateBroken(f"{rel} is not an object")
        return data

    def attempts(self, task_id: str) -> int:
        """The task's genuine reviewer rejections (attempt_cap.py's counter), 0 without one."""
        if self.attempts_dir is None:
            return 0
        try:
            data = json.loads((self.attempts_dir / f"{task_id}.json").read_text(encoding="utf-8"))
            return int(data.get("rejections") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            return 0


# --------------------------------------------------------------------------- #
# git: the change set and the contribution's tree
# --------------------------------------------------------------------------- #

def _git_bytes(args: list[str], cwd: pathlib.Path, stdin: bytes | None = None) -> tuple[int, bytes]:
    """One read-only git command with ``verify_findings._git``'s boundary, bytes out."""
    names = vf._filter_names(cwd)
    blocked = vf._unexpressible_filter(names)
    if blocked is not None:
        raise GateBroken(vf._filter_refusal(cwd, blocked))
    environ = {**os.environ, **vf._GIT_SAFE_ENV}
    environ.update(vf._filter_pairs(names, environ))
    proc = subprocess.run(["git", *vf._GIT_SAFE_CONFIG, *vf._filter_pin_args(names), *args],
                          cwd=str(cwd), input=stdin, capture_output=True, check=False,
                          env=environ)
    return proc.returncode, proc.stdout


def tree_files(root: pathlib.Path, commit: str = "HEAD",
               prefixes: tuple[str, ...] = TREE_PREFIXES) -> tuple[dict[str, bytes], list[str]]:
    """``({path: bytes}, [refusals])`` of the commit's files under ``prefixes``.

    Read from git's objects (`ls-tree`, then `cat-file --batch`), so no filter or textconv
    runs and the worktree's own files are never read: what merges is the commit.  A link
    or a submodule under a prefix is a refusal, not a file.  A ``root`` that is no git
    repository (a harvested tree at home) is read from disk instead, regular files only.
    """
    if not (root / ".git").exists():
        return disk_files(root, prefixes)
    code, out = _git_bytes(["ls-tree", "-r", "-z", "--full-tree", commit], root)
    if code != 0:
        raise GateBroken(f"git ls-tree {commit} failed in {root}")
    wanted: list[tuple[str, str]] = []
    refused: list[str] = []
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, _, raw_path = entry.partition(b"\t")
        path = raw_path.decode("utf-8", "surrogateescape")
        if not path.startswith(prefixes):
            continue
        mode, kind, sha = meta.decode("ascii", "replace").split()
        if kind != "blob" or mode not in ("100644", "100755"):
            refused.append(f"{path} is a {'link' if mode == '120000' else kind}, not a file")
            continue
        wanted.append((path, sha))
    files: dict[str, bytes] = {}
    if wanted:
        stdin = "".join(f"{sha}\n" for _path, sha in wanted).encode("ascii")
        code, out = _git_bytes(["cat-file", "--batch"], root, stdin=stdin)
        if code != 0:
            raise GateBroken(f"git cat-file --batch failed in {root}")
        at = 0
        for path, sha in wanted:
            end = out.index(b"\n", at)
            header = out[at:end].decode("ascii", "replace").split()
            if len(header) != 3 or header[0] != sha:
                raise GateBroken(f"git cat-file answered {header!r} for {path}")
            size = int(header[2])
            files[path] = out[end + 1:end + 1 + size]
            at = end + 1 + size + 1
    return files, refused


def disk_files(root: pathlib.Path, prefixes: tuple[str, ...]) -> tuple[dict[str, bytes], list[str]]:
    """:func:`tree_files` of a plain directory: its regular files under ``prefixes``."""
    files: dict[str, bytes] = {}
    refused: list[str] = []
    for prefix in prefixes:
        top = root / prefix.rstrip("/")
        if top.is_symlink() or not top.is_dir():
            continue
        for here, dirs, names in os.walk(top):
            for name in sorted(dirs + names):
                path = pathlib.Path(here) / name
                rel = path.relative_to(root).as_posix()
                mode = os.lstat(path).st_mode
                if stat.S_ISLNK(mode):
                    refused.append(f"{rel} is a link, not a file")
                elif stat.S_ISREG(mode):
                    files[rel] = path.read_bytes()
            dirs[:] = [d for d in dirs if not (pathlib.Path(here) / d).is_symlink()]
    return files, refused


def merge_base_reader(root: pathlib.Path, base: str) -> Callable[[str], str | None]:
    """``path -> its text at the merge base of base and HEAD`` (None when it has none)."""
    code, out = vf._git(["merge-base", base, "HEAD"], root)
    merge_base = out.strip() if code == 0 else ""

    def read(path: str) -> str | None:
        if not merge_base:
            return None
        code, text = vf._git(["cat-file", "blob", f"{merge_base}:{path}"], root)
        return text if code == 0 else None

    return read


_STATUS_LINE = re.compile(r"status:")
_STATUS_WORD = re.compile(r"status: [a-z][a-z_-]*")


def task_file_change(before: str | None, path: pathlib.Path) -> str:
    """Why a task file's change is more than its one status line, or '' when it is only that
    (the locate gate's rule, `verify_locate._task_file_change`)."""
    if before is None:
        return "cannot be read at the merge base"
    try:
        raw = path.read_bytes()[:MAX_TASK_FILE_BYTES + 1]
    except OSError as exc:
        return f"cannot be read ({exc})"
    if len(raw) > MAX_TASK_FILE_BYTES:
        return f"is over {MAX_TASK_FILE_BYTES} bytes"
    after = raw.decode("utf-8", errors="replace")
    statuses = [line.rstrip() for line in after.splitlines() if _STATUS_LINE.match(line)]
    if len(statuses) > 1:
        return f"has {len(statuses)} status lines; a contribution sets the one `status:` line"
    bad = next((line for line in statuses if not _STATUS_WORD.fullmatch(line)), None)
    if bad is not None:
        return f"sets its status line to something other than one status word ({bad[:80]!r})"

    def rest(text: str) -> list[str]:
        return [line.rstrip() for line in text.splitlines() if not _STATUS_LINE.match(line)]

    if rest(before) == rest(after):
        return ""
    return "changes more than its status line; a contribution sets `status:` and leaves " \
           "the plan's lines as they are"


# --------------------------------------------------------------------------- #
# rule 1: the change set
# --------------------------------------------------------------------------- #

def owners(manifests: dict[str, dict]) -> dict[str, str]:
    """``{path: task}`` of every owned path and task file of the run."""
    out: dict[str, str] = {}
    for task, manifest in manifests.items():
        out[f"tasks/{task}.yaml"] = task
        for path in manifest.get("paths") or []:
            out[str(path)] = task
    return out


def which_tasks(entries: dict[str, str], manifests: dict[str, dict], task_id: str,
                pre_merge: bool, problems: Problems) -> list[str]:
    """The task(s) the change set is judged as: the given id, else the one it belongs to."""
    if task_id:
        if task_id not in manifests:
            problems.add(1, "change set", f"{task_id!r} is not a task of this run")
            return []
        return [task_id]
    owner = owners(manifests)
    found = sorted({owner[p] for p in entries if p in owner})
    if not found:
        for path in sorted(entries):
            problems.add(1, path, "is no path a task of this run may change (each task's "
                                  "manifest lists its paths)")
        if not entries:
            problems.add(1, "change set", "is empty: nothing was written")
        return []
    if len(found) > 1 and not pre_merge:
        problems.add(1, "change set", f"belongs to {len(found)} tasks ({', '.join(found)}) and no "
                                      f"task id says which is this worker's: one worker's "
                                      f"contribution is one task's (only --pre-merge admits "
                                      f"several)")
        return []
    return found


def uncommitted_paths(root: pathlib.Path) -> set[str]:
    """The paths the worktree holds otherwise than HEAD: changed, or untracked and not
    ignored."""
    code, out = vf._git(["diff", "--no-renames", "--ignore-submodules=none", "--name-only",
                         "HEAD"], root)
    if code != 0:
        raise GateBroken(f"git diff HEAD failed in {root}")
    found = {line for line in out.splitlines() if line.strip()}
    _code, out = vf._git(["ls-files", "--others", "--exclude-standard"], root)
    return found | {line for line in out.splitlines() if line.strip()}


def check_changed(cfg: GateConfig, entries: dict[str, str], tasks: list[str],
                  manifests: dict[str, dict], root: pathlib.Path,
                  at_base: Callable[[str], str | None], problems: Problems,
                  uncommitted: set[str] = frozenset()) -> dict[str, bool]:
    """Rule 1 over the change set; ``{task: capped}`` for each task judged.  A path the
    worktree holds otherwise than HEAD (``uncommitted``) is refused: what merges is HEAD."""
    owner = owners(manifests)
    ours = set(tasks)
    for path in sorted(entries):
        status = entries[path]
        if path in uncommitted:
            problems.add(1, path, "is changed in the worktree but not committed: commit what "
                                  "you write (what merges is the commit)")
            continue
        task = owner.get(path)
        if task is None:
            problems.add(1, path, "is no path a task of this run may change (each task's "
                                  "manifest lists its paths)")
            continue
        if task not in ours:
            problems.add(1, path, f"belongs to task {task!r}, not to "
                                  f"{', '.join(sorted(ours)) or 'this contribution'}")
            continue
        if status in ("D", "R"):
            problems.add(1, path, "was deleted or renamed away; a contribution writes its own "
                                  "paths and removes nothing")
            continue
        try:
            regular = stat.S_ISREG(os.lstat(root / path).st_mode)
        except OSError:
            regular = False
        if not regular:
            problems.add(1, path, "is not a regular file in the worktree (a link is refused)")
            continue
        if path == f"tasks/{task}.yaml":
            if status == "A":
                problems.add(1, path, "adds a task file: it is on the merge target already, "
                                      "and a contribution only sets its status")
                continue
            why = task_file_change(at_base(path), root / path)
            if why:
                problems.add(1, path, why)
    capped: dict[str, bool] = {}
    for task in tasks:
        manifest = manifests[task]
        mine = {p for p in entries if owner.get(p) == task}
        at_cap = cfg.attempts(task) >= cfg.attempt_cap
        if at_cap and mine <= {f"tasks/{task}.yaml"}:
            capped[task] = True
            continue
        capped[task] = False
        for path in manifest.get("required") or []:
            if entries.get(path) not in ("A", "M", "T"):
                problems.add(1, path, f"task {task} has not written it (an unchanged task file "
                                      f"alone is accepted only at the attempt cap)")
    if not entries:
        problems.add(1, "change set", "is empty: nothing was written")
    return capped


# --------------------------------------------------------------------------- #
# rule 2: the build and the lint
# --------------------------------------------------------------------------- #

def strip_comments(text: str) -> str:
    """TeX source with every `%` comment removed, through the runtime's one definition.

    `fccommon` stands alone in an output repository, where it reads a text file as it is
    typeset, and the gate imports it: the rule that a comment is not part of the text is
    stated there once, and this is the gate's name for it.
    """
    return fc.strip_comments(text)


def lint_printed(cfg: GateConfig, path: str, text: str, problems: Problems) -> None:
    """Rule 2's second lint: what a text file may not print, and the notions it marks.

    The comments go first: they are not typeset, and a drafter's note about a declaration
    is no part of the text.  What is left may print no name of `declarations.json`, as
    `main.tex`'s introduction once printed the theorem's
    (`fccommon.printed_lean_names`), no Lean name set in a monospace font
    (`\\texttt`, `\\mathtt`, `\\ttfamily`, `\\tt`), no `\\verb`, no link
    and no URL; a notion the conventions fix is marked with `\\fcnotion`, which prints the
    words alone, and its constant must be one `declarations.json` holds.
    """
    body = strip_comments(text)
    for line, name in fc.printed_lean_names(text, set(cfg.declarations) | set(cfg.references)):
        problems.add(2, f"{path}:{line}", f"prints the Lean name {name}: the informal text names "
                                          f"no declaration of the library -- the annotations "
                                          f"supply the names -- so name it by what it states, or "
                                          f"mark it with \\fcnotion{{{name}}}{{<the words>}}, "
                                          f"whose first argument prints nothing")
    for pattern, what in PRINTED_LINT:
        found = pattern.search(body)
        if found:
            line = body.count("\n", 0, found.start()) + 1
            problems.add(2, f"{path}:{line}", f"prints {what} ({found.group(0)[:40]}): the "
                                              f"informal text names no Lean declaration and "
                                              f"links to no documentation page -- the "
                                              f"annotations supply both -- so a notion the "
                                              f"conventions fix is plain mathematics inside "
                                              f"\\fcnotion{{<constant>}}{{<the words>}}")
    for start, _end, name in fc.notion_calls(body):
        if name in cfg.declarations or name in cfg.references:
            continue
        line = body.count("\n", 0, start) + 1
        problems.add(2, f"{path}:{line}", f"\\fcnotion{{{name[:80]}}} names no constant of "
                                          f"declarations.json, as an entry or as a row of its "
                                          f"`references`")


def lint(path: str, text: str, problems: Problems) -> None:
    """Rule 2's lint of one text file, its comments included, and its `\\label` keys."""
    for pattern, what in LINT:
        found = pattern.search(text)
        if found:
            line = text.count("\n", 0, found.start()) + 1
            problems.add(2, f"{path}:{line}", f"holds {what} ({found.group(0)[:40]}): a text file "
                                              f"of the informal companion uses the marker "
                                              f"package's commands and notation.sty's, and "
                                              f"writes, reads or redefines nothing (a comment "
                                              f"is read too)")
    numbered = re.match(r"informal/sections/0*([1-9][0-9]*)-", path)
    for found in LABEL.finditer(text):
        key = found.group(1)
        if key is None:
            why = "is not letters, digits and :._- in braces"
        elif numbered and not re.match(rf"eq:{numbered.group(1)}\.[1-9][0-9]*-", key):
            why = f"is not eq:<id>-<word> of an item of section {numbered.group(1)} (STYLE.md)"
        elif not numbered and NUMBERED_LABEL.match(key):
            why = "is the form of a numbered section's label"
        else:
            continue
        line = text.count("\n", 0, found.start()) + 1
        shown = key if key is not None else text[found.end():found.end() + 40]
        problems.add(2, f"{path}:{line}", f"the \\label key {shown!r} {why}, so that no two "
                                          f"sections share a label in the whole document")


def _error_lines(text: str, limit: int = 20) -> list[str]:
    """The messages of a failed build, never the input lines TeX shows around them."""
    out = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line or re.match(r"l\.\d+ ", line) or line.startswith(" ") \
                or line.startswith("build_pdf.sh: LaTeX failed; see "):
            continue
        out.append(line[:300])
        if len(out) >= limit:
            break
    return out


def write_tree(files: dict[str, bytes], dest: pathlib.Path, prefix: str = "informal/") -> None:
    for rel, data in files.items():
        if not rel.startswith(prefix):
            continue
        path = _inside(dest, rel)
        if path is None:
            raise GateBroken(f"git named a path {rel!r} that is not a plain relative path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def stop_group(proc: subprocess.Popen) -> None:
    """SIGTERM to the program's process group, which `build_pdf.sh` passes on to latexmk's
    own group before it removes its lock; SIGKILL only if the group has not ended within
    :data:`STOP_GRACE` seconds."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        proc.wait(timeout=STOP_GRACE)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()


def run_limited(argv: list[str], cwd: pathlib.Path, timeout: int) -> tuple[int | None, str]:
    """``(exit, output)`` of a program run in a process group of its own; ``None`` at the limit.

    The group stays in the gate's session, which tend sweeps when it stops a validation (a
    detached session would escape it).  At the limit, and when the gate itself is stopped
    (:func:`on_signal` unwinds through here), the group is stopped by :func:`stop_group`.
    """
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, process_group=0)
        code: int | None = None
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        finally:
            if code is None:
                stop_group(proc)
        out.seek(0)
        return code, out.read().decode("utf-8", "replace")


def build(cfg: GateConfig, files: dict[str, bytes], scratch: pathlib.Path, only: str | None,
          problems: Problems) -> pathlib.Path | None:
    """Build `informal/` of the contribution with the gate's runtime; the OUTDIR, or None."""
    repo = scratch / "repo"
    outdir = scratch / "build"
    write_tree(files, repo)
    script = TEX / "tools" / "build_pdf.sh"
    argv = ["/bin/sh", str(script), *(["--only", only] if only else []), str(outdir)]
    code, output = run_limited(argv, repo, cfg.build_timeout)
    what = f"the build of section {only}" if only else "the build of the whole document"
    if code is None:
        problems.add(2, what, f"did not finish in {cfg.build_timeout} seconds (a section that "
                              f"loops, or one far too long)")
        return None
    if code == 3:
        raise GateBroken(f"{what} could not run: " + "; ".join(_error_lines(output, 3)))
    if code != 0:
        lines = _error_lines(output) or [f"exit {code}"]
        for line in lines:
            problems.add(2, what, line)
        return None
    return outdir


def check_aux(cfg: GateConfig, outdir: pathlib.Path, extra: list[str], problems: Problems) -> None:
    """Rule 3: the gate's `check_items.py --aux-only --strict` on the build."""
    argv = [sys.executable, "-I", str(TEX / "tools" / "check_items.py"), str(outdir),
            "--aux-only", "--strict", "--declarations", str(cfg.home / "declarations.json"),
            *extra]
    code, output = run_limited(argv, outdir.parent, cfg.build_timeout)
    if code == 0:
        return
    if code not in (1, 2):
        raise GateBroken(f"check_items.py could not run (exit {code}): "
                         + "; ".join(_error_lines(output, 3)))
    lines = [ln for ln in output.splitlines() if ln.startswith(("FAIL", "WARN", "check_items"))]
    for line in lines[:40] or [f"exit {code}"]:
        problems.add(3, "check_items.py", line.strip()[:300])


# --------------------------------------------------------------------------- #
# rules 4 and 5: the items table and the conventions
# --------------------------------------------------------------------------- #

def _json(files: dict[str, bytes], rel: str, rule: int, problems: Problems) -> Any:
    data = files.get(rel)
    if data is None:
        problems.add(rule, rel, "is not in the contribution's HEAD (commit what you write)")
        return None
    if len(data) > MAX_DOCUMENT_BYTES:
        problems.add(rule, rel, f"is over {MAX_DOCUMENT_BYTES} bytes")
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        problems.add(rule, rel, f"is not JSON ({exc})")
        return None


def check_items_table(cfg: GateConfig, files: dict[str, bytes], problems: Problems) -> None:
    """Rule 4: the items task's table, provisional, against the scope and the source."""
    doc = _json(files, "informal/items.json", 4, problems)
    if not (files.get("informal/items-review.md") or b"").strip():
        problems.add(4, "informal/items-review.md", "is empty or missing: the maintainer reads "
                                                     "the table there")
    if doc is None:
        return
    source = fi.SourceTree(require_library(cfg))
    declarations = {"declarations": list(cfg.declarations.values())}
    for line in fi.check_table(doc, declarations=declarations,
                               declarations_sha256=cfg.declarations_sha256, source=source,
                               library_commit=cfg.library_commit, numbered=False):
        problems.add(4, "informal/items.json", line)


def check_conventions(files: dict[str, bytes], problems: Problems) -> None:
    """Rule 5: hypotheses.json's shape, and a `\\fcdefhyp` of every key in notation.sty."""
    rel = "informal/hypotheses.json"
    if rel not in files:
        problems.add(5, rel, "is not in the contribution's HEAD")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "hypotheses.json"
        path.write_bytes(files[rel])
        try:
            rows, found = fc.load_hypotheses(path)
        except fc.ToolError as exc:
            problems.add(5, rel, str(exc))
            return
    for line in found:
        problems.add(5, rel, line)
    notation = strip_comments(files.get("informal/notation.sty", b"").decode("utf-8", "replace"))
    for row in rows:
        macro, key = row.get("macro"), row.get("key")
        if not isinstance(macro, str) or not isinstance(key, str):
            continue
        defined = re.search(r"\\fcdefhyp\s*\{\s*" + re.escape(macro) + r"\s*\}\s*\{\s*"
                            + re.escape(key) + r"\s*\}", notation)
        if not defined:
            problems.add(5, "informal/notation.sty", f"has no \\fcdefhyp{{{macro}}}{{{key}}} for "
                                                     f"the hypothesis {key!r} of hypotheses.json")


# --------------------------------------------------------------------------- #
# rules 6 to 8: the documents
# --------------------------------------------------------------------------- #

def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _unknown(where: str, value: dict, allowed: tuple[str, ...], rule: int,
             problems: Problems) -> None:
    for key in value:
        if key not in allowed and not str(key).startswith("x_"):
            problems.add(rule, where, f"unknown key {key!r}")


def _table(files: dict[str, bytes], cfg: GateConfig, rule: int, problems: Problems) -> dict | None:
    """items.json of the contribution, which the phase does not change (its pin holds)."""
    rel = "informal/items.json"
    data = files.get(rel)
    want = cfg.base_files.get(rel)
    if data is None or (want and _sha256_bytes(data) != want):
        problems.add(rule, rel, "is not the run's base table")
        return None
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        problems.add(rule, rel, "is not JSON")
        return None
    return doc if isinstance(doc, dict) else None


def section_items(doc: dict, section: str) -> dict[str, dict]:
    return {it["id"]: it for it in doc.get("items") or []
            if isinstance(it, dict) and it.get("section") == section and it.get("in_v1") is True}


def read_document(path: pathlib.Path, rel: str, rule: int, problems: Problems,
                  files: dict[str, bytes] | None) -> dict | None:
    """A task's JSON document: from the contribution's HEAD when the tree is given, else
    from a regular file (never through a link)."""
    if files is not None:
        return _json(files, rel, rule, problems)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            problems.add(rule, rel, "is not a regular file (a link is refused)")
            return None
        data = path.read_bytes()
    except OSError as exc:
        problems.add(rule, rel, f"cannot be read ({exc})")
        return None
    if len(data) > MAX_DOCUMENT_BYTES:
        problems.add(rule, rel, f"is over {MAX_DOCUMENT_BYTES} bytes")
        return None
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        problems.add(rule, rel, f"is not JSON ({exc})")
        return None
    return doc


def check_attack(cfg: GateConfig, manifest: dict, doc: Any, files: dict[str, bytes],
                 problems: Problems) -> None:
    """Rule 6."""
    task, rel = manifest["task_id"], f"findings/{manifest['task_id']}/attack.json"
    if not isinstance(doc, dict):
        problems.add(6, rel, "is not an object")
        return
    _unknown(rel, doc, ("schema", "schema_version", "task", "section", "direction",
                        "base_commit", "checked_items", "findings"), 6, problems)
    if doc.get("schema") != ATTACK_SCHEMA or doc.get("schema_version") != SCHEMA_VERSION:
        problems.add(6, rel, f"schema is not {ATTACK_SCHEMA} version {SCHEMA_VERSION}")
    if doc.get("task") != task:
        problems.add(6, rel, f"task is {doc.get('task')!r}, not {task!r}")
    if doc.get("section") != manifest.get("section"):
        problems.add(6, rel, f"section is {doc.get('section')!r}, not {manifest.get('section')!r}")
    if doc.get("direction") != manifest.get("direction"):
        problems.add(6, rel, f"direction is {doc.get('direction')!r}, not "
                             f"{manifest.get('direction')!r} (the task id says which)")
    if doc.get("base_commit") != cfg.base_commit:
        problems.add(6, rel, f"base_commit is not the run's {cfg.base_commit}")
    table = _table(files, cfg, 6, problems)
    section_rel = manifest.get("section_file") or ""
    section_bytes = files.get(section_rel)
    if section_bytes is None or (cfg.base_files.get(section_rel)
                                 and _sha256_bytes(section_bytes) != cfg.base_files[section_rel]):
        problems.add(6, section_rel or "section file", "is not the run's base text")
        section_text = None
    else:
        section_text = section_bytes.decode("utf-8", "replace")
    items = section_items(table, str(manifest.get("section"))) if table else {}
    checked = doc.get("checked_items")
    if not isinstance(checked, list) or sorted(map(str, checked)) != sorted(items):
        problems.add(6, rel, f"checked_items is not every in_v1 item of section "
                             f"{manifest.get('section')}: {sorted(items)}")
    findings = doc.get("findings")
    if not isinstance(findings, list):
        problems.add(6, rel, "findings is not a list")
        return
    for i, finding in enumerate(findings):
        where = f"{rel} finding {i}"
        if not isinstance(finding, dict):
            problems.add(6, where, "is not an object")
            continue
        _unknown(where, finding, ("item", "part", "severity", "quote", "declarations", "what",
                                  "lean_evidence", "fix"), 6, problems)
        item = items.get(finding.get("item"))
        if item is None:
            problems.add(6, where, f"item {finding.get('item')!r} is not an in_v1 item of the "
                                   f"section")
        else:
            letters = [p.get("letter") for p in item.get("parts") or [] if isinstance(p, dict)]
            if finding.get("part") is not None and finding.get("part") not in letters:
                problems.add(6, where, f"part {finding.get('part')!r} is not a part of item "
                                       f"{item['id']}")
        if finding.get("severity") not in SEVERITIES:
            problems.add(6, where, f"severity is not one of {', '.join(SEVERITIES)}")
        quote = finding.get("quote")
        if not _text(quote, 1000):
            problems.add(6, where, "quote is not text of at most 1000 characters")
        elif section_text is not None and quote not in section_text:
            problems.add(6, where, "quote does not occur verbatim in the section file "
                                   f"{section_rel}: copy it character for character")
        names = finding.get("declarations")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            problems.add(6, where, "declarations is not a non-empty list of names")
        else:
            for name in names:
                if name not in cfg.declarations:
                    problems.add(6, where, f"{name} is not in declarations.json")
        for field in ("what", "lean_evidence", "fix"):
            if not _text(finding.get(field)):
                problems.add(6, where, f"{field} is not text")


def attack_findings(cfg: GateConfig, manifest: dict) -> dict[str, dict]:
    """``{finding id: finding}`` of the section's attack documents (`data/attacks/`)."""
    out: dict[str, dict] = {}
    for name in manifest.get("attacks") or []:
        doc = cfg.data_document("attacks", name)
        for i, finding in enumerate(doc.get("findings") or []):
            out[f"{name}#{i}"] = finding
    return out


def check_ruling(cfg: GateConfig, manifest: dict, doc: Any, section_changed: bool | None,
                 problems: Problems) -> None:
    """Rule 7; ``section_changed`` None when the change set is not read (--findings)."""
    task, rel = manifest["task_id"], f"findings/{manifest['task_id']}/ruling.json"
    if not isinstance(doc, dict):
        problems.add(7, rel, "is not an object")
        return
    _unknown(rel, doc, ("schema", "schema_version", "task", "section", "base_commit",
                        "rulings", "requests"), 7, problems)
    if doc.get("schema") != RULING_SCHEMA or doc.get("schema_version") != SCHEMA_VERSION:
        problems.add(7, rel, f"schema is not {RULING_SCHEMA} version {SCHEMA_VERSION}")
    if doc.get("task") != task:
        problems.add(7, rel, f"task is {doc.get('task')!r}, not {task!r}")
    if doc.get("section") != manifest.get("section"):
        problems.add(7, rel, f"section is {doc.get('section')!r}, not {manifest.get('section')!r}")
    if doc.get("base_commit") != (cfg.attack_base_commit or cfg.base_commit):
        problems.add(7, rel, "base_commit is not the attack run's (the text the findings quote)")
    findings = attack_findings(cfg, manifest)
    rulings = doc.get("rulings")
    requests = doc.get("requests")
    if not isinstance(rulings, list) or not isinstance(requests, list):
        problems.add(7, rel, "rulings and requests are lists")
        return
    seen: dict[str, dict] = {}
    for i, row in enumerate(rulings):
        where = f"{rel} ruling {i}"
        if not isinstance(row, dict):
            problems.add(7, where, "is not an object")
            continue
        _unknown(where, row, ("finding", "ruling", "action", "reason"), 7, problems)
        fid = row.get("finding")
        if fid not in findings:
            problems.add(7, where, f"finding {fid!r} is not one of the section's attack findings")
            continue
        if fid in seen:
            problems.add(7, where, f"finding {fid} is ruled twice")
        seen[fid] = row
        if row.get("ruling") not in RULINGS or row.get("action") not in ACTIONS:
            problems.add(7, where, f"ruling is one of {', '.join(RULINGS)} and action one of "
                                   f"{', '.join(ACTIONS)}")
        elif (row["action"] == "none") != (row["ruling"] == "not_real"):
            problems.add(7, where, "a finding that is not real takes action none, and only it")
        if not _text(row.get("reason")):
            problems.add(7, where, "reason is not text")
        if row.get("action") == "fixed" and section_changed is False:
            problems.add(7, where, f"says fixed, and the section file "
                                   f"{manifest.get('section_file')} did not change")
    for fid in sorted(set(findings) - set(seen)):
        problems.add(7, rel, f"finding {fid} has no ruling")
    asked: set[str] = set()
    for i, req in enumerate(requests):
        where = f"{rel} request {i}"
        if not isinstance(req, dict):
            problems.add(7, where, "is not an object")
            continue
        _unknown(where, req, ("finding", "file", "what", "why"), 7, problems)
        fid = req.get("finding")
        if seen.get(fid, {}).get("action") != "out_of_scope":
            problems.add(7, where, f"finding {fid!r} is not ruled out_of_scope")
        asked.add(str(fid))
        target = req.get("file")
        if not isinstance(target, str) or not target.startswith("informal/") \
                or _inside(pathlib.Path("/"), target) is None:
            problems.add(7, where, f"file {target!r} is not a path under informal/")
        elif target == manifest.get("section_file"):
            problems.add(7, where, "names the judge's own section file: fix it there instead")
        for field in ("what", "why"):
            if not _text(req.get(field)):
                problems.add(7, where, f"{field} is not text")
    for fid, row in sorted(seen.items()):
        if row.get("action") == "out_of_scope" and fid not in asked:
            problems.add(7, rel, f"finding {fid} is out_of_scope without a request")


def judge_requests(cfg: GateConfig, manifest: dict) -> dict[str, list[dict]]:
    """``{finding id: [request, ...]}`` of the judge run (`data/rulings/`): rule 7 lets a
    finding whose fix spans files carry a request per file, and the planner gives the editor
    every file any of them names."""
    out: dict[str, list[dict]] = {}
    for name in manifest.get("rulings") or []:
        for req in cfg.data_document("rulings", name).get("requests") or []:
            if isinstance(req, dict) and isinstance(req.get("finding"), str):
                out.setdefault(req["finding"], []).append(req)
    return out


def check_edit(cfg: GateConfig, manifest: dict, doc: Any, changed_sections: set[str] | None,
               problems: Problems) -> None:
    """Rule 8's answers; ``changed_sections`` None when the change set is not read."""
    task = manifest["task_id"]
    rel = f"findings/{task}/edit.json"
    requests = judge_requests(cfg, manifest)
    if not isinstance(doc, dict):
        problems.add(8, rel, "is not an object")
    else:
        _unknown(rel, doc, ("task", "answers"), 8, problems)
        if doc.get("task") != task:
            problems.add(8, rel, f"task is {doc.get('task')!r}, not {task!r}")
        answers = doc.get("answers")
        if not isinstance(answers, list):
            problems.add(8, rel, "answers is not a list")
            answers = []
        seen: set[str] = set()
        for i, row in enumerate(answers):
            where = f"{rel} answer {i}"
            if not isinstance(row, dict):
                problems.add(8, where, "is not an object")
                continue
            _unknown(where, row, ("finding", "status", "reason"), 8, problems)
            fid = row.get("finding")
            if fid not in requests:
                problems.add(8, where, f"finding {fid!r} has no request of the judge run")
            elif fid in seen:
                problems.add(8, where, f"finding {fid} is answered twice (one answer covers "
                                       f"all of its requests)")
            seen.add(str(fid))
            if row.get("status") not in ANSWERS:
                problems.add(8, where, f"status is not one of {', '.join(ANSWERS)}")
            if not _text(row.get("reason")):
                problems.add(8, where, "reason is not text")
        for fid in sorted(set(requests) - seen):
            problems.add(8, rel, f"the request of finding {fid} is not answered")
    named = {str(r.get("file")) for reqs in requests.values() for r in reqs}
    for path in sorted(changed_sections or ()):
        if path not in named:
            problems.add(8, path, "is a section file no request of the judge run names")


def check_proposal(manifest: dict, proposal: Any, table: dict | None, problems: Problems) -> None:
    """Rule 8's `items-proposal.json`: changes to the table, for the maintainer to apply."""
    task = manifest["task_id"]
    prel = f"findings/{task}/items-proposal.json"
    if not isinstance(proposal, dict):
        problems.add(8, prel, "is not an object")
        return
    _unknown(prel, proposal, ("task", "changes"), 8, problems)
    if proposal.get("task") != task:
        problems.add(8, prel, f"task is {proposal.get('task')!r}, not {task!r}")
    ids = {it.get("id") for it in (table or {}).get("items") or [] if isinstance(it, dict)}
    changes = proposal.get("changes")
    if not isinstance(changes, list):
        problems.add(8, prel, "changes is not a list")
        return
    for i, change in enumerate(changes):
        where = f"{prel} change {i}"
        if not isinstance(change, dict):
            problems.add(8, where, "is not an object")
            continue
        _unknown(where, change, ("item", "field", "from", "to", "why"), 8, problems)
        if table is not None and change.get("item") not in ids:
            problems.add(8, where, f"item {change.get('item')!r} is not in items.json")
        if not _text(change.get("field"), 200) or not _text(change.get("why")):
            problems.add(8, where, "field and why are text")
        if "from" not in change or "to" not in change:
            problems.add(8, where, "names its from and to values")


# --------------------------------------------------------------------------- #
# rule 9: copying
# --------------------------------------------------------------------------- #

_MATH = re.compile(r"\$\$.*?\$\$|\$.*?\$|\\\[.*?\\\]|\\\(.*?\\\)", re.S)
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def tex_words(text: str) -> list[str]:
    """The words of TeX prose: comments, math and control sequences dropped, lower case."""
    text = _MATH.sub(" ", strip_comments(text))
    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    return _WORD.findall(text.lower())


def comment_words(lines: list[str]) -> list[list[str]]:
    """The words of each docstring and comment of a Lean file, code spans dropped."""
    text = "\n".join(lines)
    blocks = re.findall(r"/-[-!]?(.*?)-/", text, re.S)
    blocks += re.findall(r"--(.*)", re.sub(r"/-.*?-/", " ", text, flags=re.S))
    out = []
    for block in blocks:
        block = re.sub(r"`[^`]*`", " ", block)
        block = re.sub(r"\$.*?\$", " ", block, flags=re.S)
        words = _WORD.findall(block.lower())
        if words:
            out.append(words)
    return out


def check_copying(cfg: GateConfig, changed_text: dict[str, str], item_ids: list[str] | None,
                  table: dict | None, problems: Problems) -> None:
    """Rule 9 over the text files a task wrote, against the docstrings and comments of the
    library files of its items' declarations (every in_v1 item when ``item_ids`` is None)."""
    if not changed_text or table is None:
        return
    n = cfg.copy_run_words
    files: set[str] = set()
    for item in table.get("items") or []:
        if not isinstance(item, dict) or item.get("in_v1") is not True:
            continue
        if item_ids is not None and item.get("id") not in item_ids:
            continue
        for decl in item.get("declarations") or []:
            row = cfg.declarations.get(decl.get("name")) if isinstance(decl, dict) else None
            if row and isinstance(row.get("path"), str):
                files.add(row["path"])
    source = fi.SourceTree(require_library(cfg))
    shingles: dict[tuple[str, ...], str] = {}
    for path in sorted(files):
        for words in comment_words(source.lines(path) or []):
            for i in range(len(words) - n + 1):
                shingles.setdefault(tuple(words[i:i + n]), path)
    if not shingles:
        return
    for rel, text in sorted(changed_text.items()):
        words = tex_words(text)
        for i in range(len(words) - n + 1):
            found = shingles.get(tuple(words[i:i + n]))
            if found:
                problems.add(9, rel, f"shares a run of {n} words with a docstring or comment of "
                                     f"{found} (\"{' '.join(words[i:i + n])}\"): write the "
                                     f"statement in your own words; a docstring may quote the "
                                     f"reference work")
                break


def require_library(cfg: GateConfig) -> pathlib.Path:
    if cfg.library_src is None or not cfg.library_src.is_dir():
        raise GateBroken(f"the library's source {cfg.library_src} is missing (cf run ships it "
                         f"with --include-sources)")
    return cfg.library_src


# --------------------------------------------------------------------------- #
# one task
# --------------------------------------------------------------------------- #

def judge_task(cfg: GateConfig, manifest: dict, files: dict[str, bytes],
               entries: dict[str, str] | None, problems: Problems) -> None:
    """Rules 2 to 9 for one task of a change set (``entries`` its own changed paths)."""
    phase = cfg.phase
    entries = entries or {}
    task = manifest["task_id"]
    changed_tex = {p: files[p].decode("utf-8", "replace") for p in entries
                   if p.startswith("informal/") and p.endswith((".tex", ".sty")) and p in files}
    if phase == "items":
        check_items_table(cfg, files, problems)
        return
    if phase == "attack":
        doc = read_document(pathlib.Path(), f"findings/{task}/attack.json", 6, problems, files)
        if doc is not None:
            check_attack(cfg, manifest, doc, files, problems)
        return
    table = _table(files, cfg, 1, problems) if phase != "edit" else _json(
        files, "informal/items.json", 8, Problems())
    section_file = manifest.get("section_file")
    if phase == "judge":
        doc = read_document(pathlib.Path(), f"findings/{task}/ruling.json", 7, problems, files)
        if doc is not None:
            check_ruling(cfg, manifest, doc, section_file in entries, problems)
    if phase == "edit":
        doc = read_document(pathlib.Path(), f"findings/{task}/edit.json", 8, problems, files)
        prel = f"findings/{task}/items-proposal.json"
        proposal = _json(files, prel, 8, problems) if prel in files else None
        sections = {p for p in entries if p.startswith("informal/sections/")
                    and p not in CONVENTION_FILES}
        if doc is not None:
            check_edit(cfg, manifest, doc, sections, problems)
        if proposal is not None:
            check_proposal(manifest, proposal, table, problems)
        if cfg.base_files.get("informal/items.json") and _sha256_bytes(
                files.get("informal/items.json", b"")) != cfg.base_files["informal/items.json"]:
            problems.add(8, "informal/items.json", "changed: a change to the table is a proposal "
                                                   "(items-proposal.json) for the maintainer")
    if phase == "conventions" or (phase == "edit" and "informal/hypotheses.json" in entries):
        check_conventions(files, problems)
    for rel, text in sorted(changed_tex.items()):
        lint(rel, text, problems)
        lint_printed(cfg, rel, text, problems)
    if len(problems):
        return
    must_build = phase in WHOLE_BUILDS or (phase == "draft") or \
        (phase == "judge" and section_file in entries)
    if must_build:
        with tempfile.TemporaryDirectory(prefix="cf-informal-gate-") as tmp:
            only = manifest.get("section") if phase in SECTION_BUILDS else None
            outdir = build(cfg, files, pathlib.Path(tmp), only, problems)
            if outdir is not None:
                extra = (["--section", str(only)] if only else
                         ["--conventions"] if phase == "conventions" else [])
                check_aux(cfg, outdir, extra, problems)
    item_ids = manifest.get("items") if phase in SECTION_BUILDS else None
    check_copying(cfg, {p: t for p, t in changed_tex.items() if p.endswith(".tex")},
                  item_ids, table, problems)


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

CALLER_VALIDATION = "validation"
CALLER_PRE_MERGE = "pre_merge"
CALLER_FINDINGS = "findings_check"


def write_log(cfg: GateConfig, record: dict) -> None:
    """Append one line to the gate log; any failure to write is silent by design."""
    if cfg.log_dir is None:
        return
    try:
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(cfg.log_dir / "gate.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            try:
                import fcntl  # noqa: PLC0415 - POSIX only
                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        return


#: the one document of a single-task phase that --findings checks, and its rule
PHASE_DOCUMENTS = {"items": ("informal/items.json", 4), "conventions": ("informal/hypotheses.json", 5)}


def run_findings(cfg: GateConfig, documents: list[pathlib.Path], root: pathlib.Path,
                 problems: Problems) -> int:
    """Rules 4 to 8 on named documents alone; the number checked.  `informal/` is read from
    ``root`` as it lies on disk: the worktree, or at home the run's planned `repo/`, beside
    which the harvested documents are named by their own paths (a document outside ``root``
    is `findings/<its folder>/<its name>`)."""
    files, _refused = disk_files(root, ("informal/",))
    for doc_path in documents:
        path = doc_path if doc_path.is_absolute() else root / doc_path
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = f"findings/{path.parent.name}/{path.name}"
        single = PHASE_DOCUMENTS.get(cfg.phase)
        if single is not None:
            if rel != single[0]:
                problems.add(single[1], rel, f"is not {single[0]}, the {cfg.phase} task's "
                                             f"document")
            elif cfg.phase == "items":
                check_items_table(cfg, files, problems)
            else:
                check_conventions(files, problems)
            continue
        name = path.name
        manifest = cfg.manifest(path.parent.name)
        if manifest is None or rel != f"findings/{path.parent.name}/{name}":
            problems.add(6, rel, f"is not findings/<task>/{name} of a task of this run")
            continue
        doc = read_document(path, rel, 6, problems, None)
        if doc is None:
            continue
        if cfg.phase == "attack" and name == "attack.json":
            check_attack(cfg, manifest, doc, files, problems)
        elif cfg.phase == "judge" and name == "ruling.json":
            check_ruling(cfg, manifest, doc, None, problems)
        elif cfg.phase == "edit" and name == "edit.json":
            check_edit(cfg, manifest, doc, None, problems)
        elif cfg.phase == "edit" and name == "items-proposal.json":
            check_proposal(manifest, doc, _json(files, "informal/items.json", 8, Problems()),
                           problems)
        else:
            problems.add(6, rel, f"is no document a {cfg.phase} task writes")
    return len(documents)


def run_gate(cfg: GateConfig, *, root: pathlib.Path, findings: list[pathlib.Path] | None = None,
             changed_files: bool = False, base: str = "main", task_id: str = "",
             task_id_from: str | None = None, pre_merge: bool = False) -> dict:
    """Run the gate in-process; ``{exit, problems, broken, tasks, capped, ownership, ...}``."""
    result: dict[str, Any] = {"exit": EXIT_OK, "problems": [], "broken": [], "tasks": [],
                              "capped": [], "ownership": "", "phase": cfg.phase,
                              "run_id": cfg.run_id, "documents": 0}
    problems = Problems()
    how = (task_id_from or "--task-id") if task_id else ""
    try:
        if findings:
            result["documents"] = run_findings(cfg, findings, root, problems)
        elif changed_files:
            entries, errs = vf.changed_entries(root, base)
            if errs:
                raise GateBroken("; ".join(errs))
            manifests = cfg.manifests()
            if not task_id:
                task_id, how = vf.task_from_orchestrator(cfg.orchestrator_db, root)
                if not task_id:
                    result["ownership_note"] = how
                    how = ""
            tasks = which_tasks(entries, manifests, task_id, pre_merge, problems)
            if tasks and not task_id and len(tasks) == 1:
                how = "the change set"
            result["tasks"] = tasks
            if tasks:
                capped = check_changed(cfg, entries, tasks, manifests, root,
                                       merge_base_reader(root, base), problems,
                                       uncommitted_paths(root))
                result["capped"] = sorted(t for t, c in capped.items() if c)
                if not len(problems):
                    files, refused = tree_files(root, "HEAD")
                    for line in refused:
                        problems.add(2, "HEAD", line)
                    owner = owners(manifests)
                    for task in tasks:
                        if capped[task] or len(problems):
                            continue
                        mine = {p: s for p, s in entries.items() if owner.get(p) == task}
                        judge_task(cfg, manifests[task], files, mine, problems)
            result["ownership"] = (f"ownership checked against task {', '.join(tasks)}"
                                   + (f" (task id from {how})" if how else "")) if tasks else \
                "ownership: no task of this run claims the change set"
        else:
            raise GateBroken("give --changed-files, --findings DOC or --self-check")
    except GateBroken as exc:
        result["broken"].append(str(exc))
        result["exit"] = EXIT_GATE_BROKEN
    result["problems"] = problems.lines()
    result["by_rule"] = problems.by_rule()
    if result["exit"] == EXIT_OK and len(problems):
        result["exit"] = EXIT_REJECTED
    result["caller"] = (CALLER_FINDINGS if findings else
                        CALLER_PRE_MERGE if pre_merge else CALLER_VALIDATION)
    write_log(cfg, {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "run_id": cfg.run_id, "phase": cfg.phase, "task_id": task_id,
                    "tasks": result["tasks"], "task_id_from": how or None,
                    "caller": result["caller"], "changed_files": bool(changed_files),
                    "pre_merge": bool(pre_merge), "capped": result["capped"],
                    "ok": result["exit"] == EXIT_OK, "exit": result["exit"],
                    "by_rule": result["by_rule"], "broken": result["broken"][:3],
                    "reasons": [p[:300] for p in result["problems"][:20]]})
    return result


def self_check(cfg: GateConfig) -> str:
    """What this gate needs before a contribution reaches it; a summary, or :class:`GateBroken`."""
    manifests = cfg.manifests()
    if not manifests:
        raise GateBroken(f"{cfg.tasks_dir} holds no task manifest")
    require_library(cfg)
    tex = ""
    if cfg.phase in SECTION_BUILDS + WHOLE_BUILDS:
        missing = [p for p in ("pdflatex", "latexmk", "kpsewhich") if shutil.which(p) is None]
        if missing:
            raise GateBroken("not on PATH: " + ", ".join(missing))
        absent = []
        for name in cfg.tex_packages:
            found = subprocess.run(["kpsewhich", name], capture_output=True, text=True,
                                   check=False)
            if found.returncode != 0 or not found.stdout.strip():
                absent.append(name)
        if absent:
            raise GateBroken("TeX cannot find: " + ", ".join(absent))
        version = subprocess.run(["pdflatex", "--version"], capture_output=True, text=True,
                                 check=False).stdout.splitlines()
        tex = f", {version[0] if version else 'pdflatex'}, {len(cfg.tex_packages)} TeX file(s) found"
    return (f"OK: gate self-check: {cfg.phase} run {cfg.run_id}, {len(manifests)} task "
            f"manifest(s), {len(cfg.files)} pinned file(s) as planned, the library's source "
            f"at {cfg.library_src}{tex}")


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #

class UsageRefused(Exception):
    """A command line the gate refuses: exit 3, before anything is read or logged."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: D401 - argparse's hook
        raise UsageRefused(message)


class _Once(argparse.Action):
    """An option that may be given once: a second ``--gate-config`` is refused."""

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, f"_{self.dest}_given", False):
            raise UsageRefused(f"{option_string} is given twice; the gate takes one config")
        setattr(namespace, f"_{self.dest}_given", True)
        setattr(namespace, self.dest, values)


def check_command_line(args: argparse.Namespace, here: pathlib.Path = HERE) -> None:
    """``--pre-merge`` only with ``--changed-files``; in a gate directory only its own config."""
    if args.pre_merge and not args.changed_files:
        raise UsageRefused("--pre-merge is tend's pre-merge call and comes with --changed-files")
    beside = here / "gate.json"
    if beside.is_file():
        given = pathlib.Path(args.gate_config).expanduser()
        try:
            same = given.resolve() == beside.resolve()
        except OSError:
            same = False
        if not same:
            raise UsageRefused(f"--gate-config {given} is not {beside}, the config beside this "
                               f"gate: a gate directory runs with its own gate.json only")


STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def on_signal(signum: int, _frame: Any) -> None:
    """SIGTERM or SIGINT (tend stopping a validation): unwind, so that a running build's
    group is stopped and the scratch directory removed before the gate exits; a second
    signal does not cut that short."""
    for each in STOP_SIGNALS:
        signal.signal(each, signal.SIG_IGN)
    raise SystemExit(128 + signum)


def _broken(message: str) -> int:
    print(f"FAIL: {message} — the gate could not run: this is not a finding of yours",
          file=sys.stderr)
    print("ownership: not checked (the gate could not run)")
    return EXIT_GATE_BROKEN


def main(argv: list[str] | None = None) -> int:
    """The CLI; any failure of the gate itself is exit 3, never the interpreter's 1."""
    try:
        return _main(argv)
    except Exception as exc:  # noqa: BLE001 - a crash of the gate is not a verdict
        return _broken(f"the gate itself failed ({type(exc).__name__}: {exc})")


def _main(argv: list[str] | None = None) -> int:
    ap = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                 allow_abbrev=False)
    ap.add_argument("--gate-config", default=str(HERE / "gate.json"), action=_Once,
                    help="the informalize gate.json (default: next to this script; given "
                         "once, and in a gate directory only that file)")
    ap.add_argument("--root", default=".", help="the worktree")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--findings", action="append", default=[],
                       help="check this task document only (repeatable): "
                            "findings/<task>/<name>.json")
    which.add_argument("--changed-files", action="store_true",
                       help="judge the contribution's change set by every rule")
    ap.add_argument("--base", default="main", help="the branch the change set is read against")
    ap.add_argument("--task-id", default=None,
                    help="the worker's own task id (default: $TEND_TASK_ID, else "
                         "orchestrator.sqlite, else the one task of the change set)")
    ap.add_argument("--pre-merge", action="store_true",
                    help="with --changed-files: the batched pre-merge check, where several "
                         "tasks' contributions are judged together")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-check", action="store_true",
                    help="check that this gate can run and judge nothing")
    try:
        args = ap.parse_args(argv)
        check_command_line(args)
    except UsageRefused as exc:
        return _broken(f"the command line is refused ({exc})")
    if IMPORT_ERRORS:
        return _broken("the gate directory is incomplete: " + "; ".join(IMPORT_ERRORS)
                       + " (cf plan-informalize stages verify_informal.py with "
                         "verify_findings.py and tex/ beside it)")
    try:
        cfg = GateConfig.load(pathlib.Path(args.gate_config).expanduser().resolve())
        if args.self_check:
            print(self_check(cfg))
            print("ownership: not checked (--self-check judges no contribution)")
            return EXIT_OK
    except GateBroken as exc:
        return _broken(str(exc))
    if args.task_id:
        task_id, task_id_from = args.task_id, "--task-id"
    else:
        task_id = os.environ.get("TEND_TASK_ID", "")
        task_id_from = "$TEND_TASK_ID" if task_id else None
    for signum in STOP_SIGNALS:
        try:
            signal.signal(signum, on_signal)
        except ValueError:        # an in-process caller off the main thread: nothing to stop
            pass
    result = run_gate(cfg, root=pathlib.Path(args.root).resolve(),
                      findings=[pathlib.Path(f) for f in args.findings],
                      changed_files=args.changed_files, base=args.base, task_id=task_id,
                      task_id_from=task_id_from, pre_merge=args.pre_merge)
    for broken in result["broken"]:
        print(f"FAIL: {broken} — the gate could not run: this is not a finding of yours",
              file=sys.stderr)
    if result["problems"]:
        print(f"VALIDATION FAILED ({len(result['problems'])} problem(s)):", file=sys.stderr)
        for line in result["problems"]:
            print("  - " + line, file=sys.stderr)
    elif result["exit"] == EXIT_OK and not args.quiet:
        if result["documents"]:
            print(f"OK: {result['documents']} {cfg.phase} document(s) of run {cfg.run_id} "
                  f"keep rules 6 to 8")
        else:
            capped = result["capped"]
            print(f"OK: the {cfg.phase} contribution of {', '.join(result['tasks'])} passes "
                  f"every rule" + (f"; {', '.join(capped)} at the attempt cap, its files as "
                                   f"they were" if capped else ""))
    if result["ownership"]:
        print(f"  changed files: {result['ownership']}")
    elif result["broken"]:
        print("ownership: not checked (the gate could not run)")
    else:
        print("ownership: not checked (--findings: these documents were checked, the change "
              "set was not)")
    return int(result["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
