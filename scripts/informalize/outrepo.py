"""The output repository, `informalize/<id>/repo/`, and where a write into it may land.

New code, for docs/proposals/informalize.md sections 1.3 and 4.  The skill writes into no
repository but this one, and only under the exception section 1.3 words (SKILL.md rule
10, CLAUDE.md section 4, CONTRIBUTING.md invariant 3, docs/overlay.md rule 5):

* `cf informalize init` creates the repository inside the workspace and stamps it:
  `informal/informalize.json` names the id.  Every other write refuses a repository
  without that stamp, or with another id's (:meth:`OutputRepo.require_stamp`).
* Only the paths of :data:`ALLOWED_DIRS` and :data:`ALLOWED_FILES` are written
  (:meth:`OutputRepo.target`); a `repo/` that is a symlink or resolves outside
  `informalize/<id>/` is refused, and so is a write through a symlinked component.
* git runs there only through `cflib.gitsafe`, which pins every program a repository's
  configuration could name, and a commit takes only the paths the command wrote
  (:meth:`OutputRepo.commit`), so a maintainer's uncommitted edit elsewhere stays his.

Nothing here reads or writes the library: that is `library/`, beside the repository.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from cflib import gitsafe
from cflib.errors import CfError
from cflib.paths import WorkspacePaths

#: directories a command may write under (section 1.3), relative to the repository
ALLOWED_DIRS = ("informal", "annotations", "pdf-overlay", ".github/workflows", ".lake/overlay-pdf")
#: single files a command may write
ALLOWED_FILES = ("site/index.html", "README.md", "LICENSE", ".gitignore")
#: the stamp: the runtime's configuration names the id the repository was made for
STAMP = "informal/informalize.json"
#: the identity of a commit when the maintainer's git names none (a fresh machine, the
#: tests); `-c` settings, because gitsafe.run takes no environment of its own
FALLBACK_IDENTITY = ("-c", "user.name=cf informalize",
                     "-c", "user.email=cf-informalize@example.invalid")
_GIT_SETTINGS = ("-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main")


class OutputRepo:
    """One informalize id's output repository."""

    def __init__(self, paths: WorkspacePaths, informalize_id: str):
        self.id = informalize_id
        self.home = paths.informalize_dir(informalize_id)
        self.root = paths.informalize_repo(informalize_id)
        self._who: tuple[str, ...] | None = None

    # -- where it is ------------------------------------------------------
    def check_location(self) -> Path:
        """The repository's path, refusing a `repo/` that is a link or lives elsewhere."""
        if self.home.is_symlink():
            raise CfError(f"{self.home} is a symlink",
                          hint="informalize/<id>/ is written in place; remove the link")
        if self.root.is_symlink():
            raise CfError(f"{self.root} is a symlink",
                          hint="the output repository is written in place; a link could put "
                               "the write anywhere. Remove it and re-run")
        if self.root.exists():
            if not self.root.is_dir():
                raise CfError(f"{self.root} is not a directory")
            if Path(os.path.realpath(self.root)) != Path(os.path.realpath(self.home)) / "repo":
                raise CfError(f"{self.root} resolves outside {self.home}",
                              hint="repo/ must be a real directory of informalize/<id>/")
        return self.root

    def exists(self) -> bool:
        return (self.check_location() / ".git").exists()

    def stamped_id(self) -> str | None:
        """The id `informal/informalize.json` names, or None without a readable stamp."""
        path = self.root / STAMP
        if path.is_symlink() or not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        found = value.get("id") if isinstance(value, dict) else None
        return found if isinstance(found, str) else None

    def require_stamp(self) -> None:
        """Refuse a repository `init` did not make for this id."""
        self.check_location()
        if not (self.root / ".git").exists():
            raise CfError(f"{self.root} is not a git repository",
                          hint=f"`cf informalize init {self.id} ...` creates it")
        found = self.stamped_id()
        if found != self.id:
            raise CfError(f"{self.root} carries no stamp for {self.id!r} ({STAMP} names "
                          f"{found!r})",
                          hint="cf informalize writes only into the repository init made for "
                               "this id (docs/proposals/informalize.md section 1.3)")

    # -- writing ----------------------------------------------------------
    @staticmethod
    def allowed(relative: str) -> bool:
        return relative in ALLOWED_FILES or any(
            relative.startswith(d + "/") for d in ALLOWED_DIRS)

    def target(self, relative: str) -> Path:
        """`repo/<relative>`, or a refusal: outside the allowed paths, not normalised, or
        through a symlinked component."""
        pure = PurePosixPath(relative)
        if not relative or relative != relative.strip() or pure.is_absolute() \
                or str(pure) != relative or "\\" in relative \
                or any(part in ("", ".", "..") for part in pure.parts):
            raise CfError(f"{relative!r} is not a path inside the output repository")
        if not self.allowed(relative):
            raise CfError(f"{relative} is not a path cf informalize may write",
                          hint="only informal/, annotations/, pdf-overlay/, site/index.html, "
                               ".github/workflows/, .lake/overlay-pdf/, README.md, LICENSE and "
                               ".gitignore (docs/proposals/informalize.md section 1.3)")
        current = self.check_location()
        for part in pure.parts:
            current = current / part
            if current.is_symlink():
                raise CfError(f"{current} is a symlink",
                              hint="nothing in the output repository is written through a link")
        return current

    def read_bytes(self, relative: str) -> bytes | None:
        path = self.root / relative
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes()

    def write(self, relative: str, data: bytes) -> bool:
        """Write `relative` atomically; False when it already held exactly these bytes."""
        path = self.target(relative)
        if path.is_file() and path.read_bytes() == data:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                                             suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(data)
            # mkstemp makes it 0600; the tools run through bash and python3, never by mode
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:                                  # pragma: no cover
                pass
            raise
        return True

    def remove(self, relative: str) -> bool:
        """Delete `relative` and the directories it leaves empty; False when it is absent."""
        path = self.target(relative)
        if not path.exists():
            return False
        path.unlink()
        parent = path.parent
        while parent != self.root and self.root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return True

    # -- git ----------------------------------------------------------------
    def git(self, *args: str, check: bool = True) -> str:
        """One git command in the repository, through `cflib.gitsafe`."""
        if shutil.which("git") is None:
            raise CfError("git not found on PATH",
                          hint="the output repository is a git repository")
        done = gitsafe.run(self.root, [*_GIT_SETTINGS, *self._identity(), *args], timeout=120)
        if check and done.returncode != 0:
            raise CfError(f"git {' '.join(args[:2])} failed in {self.root}: "
                          f"{(done.stderr or done.stdout).strip()}")
        return done.stdout

    def _identity(self) -> tuple[str, ...]:
        """No `-c` identity when the maintainer's git names one (his commits are his)."""
        if not (self.root / ".git").exists():
            return ()
        if self._who is None:
            names = []
            for key in ("user.name", "user.email"):
                done = gitsafe.run(self.root, ["config", "--get", key], timeout=30)
                names.append(done.returncode == 0 and bool(done.stdout.strip()))
            self._who = () if all(names) else FALLBACK_IDENTITY
        return self._who

    def init(self) -> None:
        self.check_location().mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self.git("init", "-q")

    def head(self) -> str | None:
        if not (self.root / ".git").exists():
            return None
        done = gitsafe.run(self.root, ["rev-parse", "--verify", "-q", "HEAD"], timeout=30)
        found = done.stdout.strip()
        return found if done.returncode == 0 and len(found) == 40 else None

    def tracked(self) -> set[str]:
        """Every path the index holds."""
        if not (self.root / ".git").exists():
            return set()
        return {p for p in self.git("ls-files", "-z").split("\0") if p}

    def changes(self, paths: list[str] | None = None) -> list[str]:
        """Every path git reports as changed, staged or untracked (all of them, or of `paths`)."""
        args = ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
        if paths is not None:
            if not paths:
                return []
            args += ["--", *paths]
        fields = self.git(*args).split("\0")
        out: list[str] = []
        i = 0
        while i < len(fields):
            entry = fields[i]
            i += 1
            if len(entry) < 4:
                continue
            out.append(entry[3:])
            if entry[0] in "RC" and i < len(fields):         # a rename's source path follows
                out.append(fields[i])
                i += 1
        return out

    def commit(self, paths: list[str], message: str) -> str | None:
        """Commit exactly `paths` (written or deleted); None when none of them changed."""
        for relative in paths:
            self.target(relative)                            # the same refusals as a write
        present = [p for p in paths if (self.root / p).exists()]
        gone = [p for p in paths if not (self.root / p).exists()]
        if present:
            self.git("add", "--", *present)
        if gone:
            self.git("rm", "-q", "--cached", "--ignore-unmatch", "--", *gone)
        changed = self.changes(paths)
        if not changed:
            return None
        self.git("commit", "-q", "-m", message, "--", *changed)
        return self.head()
