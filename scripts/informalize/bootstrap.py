"""Stage I0: `cf informalize init` and `cf informalize sync`.

New code, for docs/proposals/informalize.md sections 3 (I0), 4 and 7.1.  `init` writes
`informalize/<id>/informalize.yaml` from its flags, creates the output repository
`informalize/<id>/repo/` (git init), renders the project files and the license once,
copies the runtime in, commits, and writes the I0 receipt.  `sync` copies the runtime
again (a no-op when nothing changed), writes `informal/informalize.json` from the
settings, commits what it changed, and writes the receipt again.  Both run git in the
output repository only through `cflib.gitsafe`, and write only under the exception of
section 1.3 (`informalize.outrepo`).

A second `init` with the same settings changes nothing; with other settings it refuses
unless `--force`, which rewrites `informalize.yaml` (one that does not parse too, and
`LICENSE` when the text's license changed) but never a project file, which is the
maintainer's from the first `init` on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from cflib import log
from cflib.errors import CfError
from cflib.hashing import sha256_file

from informalize import config, project, runtime, stages
from informalize.outrepo import OutputRepo

#: what `cf init`'s .gitignore ignores of an informalize directory (section 4); a workspace
#: made before this mode existed gets the lines from the first `init`
WORKSPACE_IGNORES = ("informalize/*/repo/", "informalize/*/library/",
                     "informalize/*/verso/", "informalize/*/pdf/")
IGNORE_COMMENT = ("# Informalize: the output repository is its own git repository (cf informalize\n"
                  "# init), and the library's copy, the Verso build's folder and the canonical PDF\n"
                  "# are rebuilt from the host:\n")


@dataclass
class Result:
    """What `init` or `sync` did."""

    repo: Path
    written: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    commit: str | None = None
    head: str | None = None
    yaml_written: bool = False
    notes: list[str] = field(default_factory=list)


def ensure_workspace_ignores(workspace) -> bool:
    """Append the informalize lines to the workspace's .gitignore when it lacks them."""
    path = workspace.paths.gitignore
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    have = {line.strip() for line in text.splitlines()}
    missing = [line for line in WORKSPACE_IGNORES if line not in have]
    if not missing:
        return False
    tail = "" if text.endswith("\n") or not text else "\n"
    path.write_text(text + tail + "\n" + IGNORE_COMMENT + "".join(f"{m}\n" for m in missing),
                    encoding="utf-8")
    return True


#: what `init --force` reads an informalize.yaml that does not parse as
UNREADABLE = "a file that does not parse"


def _text_license(settings: object) -> str | None:
    text = settings.get("text") if isinstance(settings, dict) else None
    found = text.get("license") if isinstance(text, dict) else None
    return found if isinstance(found, str) else None


def _differences(old: object, new: dict, prefix: str = "") -> list[str]:
    if not isinstance(old, dict):
        return [prefix.rstrip(".") or "the whole file"]
    out = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            out += _differences(a, b, f"{prefix}{key}.")
        elif a != b:
            out.append(f"{prefix}{key}")
    return out


def keep_discovered_license(existing: object, settings: dict) -> dict:
    """`settings` with the library license stage I1 read from the license file, when its
    flags name none: a second `init` with the same flags is then the same settings."""
    old = existing.get("library") if isinstance(existing, dict) else None
    new = settings["library"]
    if not isinstance(old, dict) or new.get("license") is not None \
            or old.get("license_method") is not None or not old.get("license") \
            or any(old.get(k) != new.get(k) for k in ("url", "commit")):
        return settings
    kept = {**settings, "library": {**new, "license": old["license"]}}
    return kept if not config.problems(kept) else settings


def _check_new_repo(repo: OutputRepo, settings: dict) -> None:
    """A repository `init` may create or take up again: absent, empty, its own, or one
    whose first `init` stopped before the stamp, holding nothing init would not write."""
    root = repo.check_location()
    if not root.exists() or not any(root.iterdir()):
        return
    if repo.git_dir() is None:
        raise CfError(f"{root} exists and is not a git repository",
                      hint="init creates the output repository itself; move that directory away")
    stamped = repo.stamped_id()
    if stamped == repo.id:
        return
    if stamped is None and repo.head() is None:
        foreign = _not_written_by_init(repo, settings)
        if not foreign:
            return
        raise CfError(f"{root} is a git repository init did not make: it holds "
                      + ", ".join(foreign[:3]) + (" ..." if foreign[3:] else ""),
                      hint="init takes up only an empty repository or what its own first run "
                           "left; move that directory away")
    raise CfError(f"{root} is a repository init did not make for {repo.id!r} (its stamp names "
                  f"{stamped!r})",
                  hint="cf informalize writes only into its own output repository "
                       "(docs/proposals/informalize.md section 1.3)")


def _not_written_by_init(repo: OutputRepo, settings: dict) -> list[str]:
    """Every file of `repo` outside `.git` that is not a file init writes, with the bytes it
    writes (the project files, LICENSE, the runtime)."""
    expected = dict(project.project_files(settings))
    expected.update({f"{runtime.INFORMAL}/{path}": raw
                     for path, raw in runtime.template_files().items()})
    out = []
    for parent, names, files in os.walk(repo.root):
        names[:] = sorted(n for n in names if not (parent == str(repo.root) and n == ".git"))
        for name in sorted(files + [n for n in names if os.path.islink(os.path.join(parent, n))]):
            path = Path(parent) / name
            relative = path.relative_to(repo.root).as_posix()
            raw = None if path.is_symlink() or not path.is_file() else path.read_bytes()
            if relative == "LICENSE" and project.license_matches(settings, raw):
                continue
            if raw is None or expected.get(relative) != raw:
                out.append(relative)
    return out


def write_receipt(workspace, repo: OutputRepo) -> Path:
    """`stages/I0.json`: the settings read, and the two files of informal/ only `sync` writes."""
    home = repo.home
    outputs = {}
    for relative in (runtime.STAMP, f"{runtime.INFORMAL}/{runtime.GENERATED_NAME}"):
        outputs[f"repo/{relative}"] = sha256_file(repo.root / relative)
    return stages.write_receipt(
        workspace.paths, repo.id, "I0",
        inputs={"informalize.yaml": sha256_file(home / "informalize.yaml")},
        outputs=outputs, checks={"runtime": "current", "stamp": repo.id})


def init(workspace, settings: dict, *, force: bool = False) -> Result:
    """`cf informalize init`: the settings file, the repository, the project, the runtime."""
    paths = workspace.paths
    sessions = config.session_limit(workspace)
    found = config.problems(settings, sessions=sessions)
    if found:
        raise CfError(found[0], hint="; ".join(found[1:4]) or None)
    iid = settings["id"]
    yaml_path = paths.informalize_yaml(iid)
    try:
        existing = config.read(yaml_path) if yaml_path.is_file() else None
    except CfError:
        if not force:
            raise
        existing = UNREADABLE                   # --force replaces a file that does not parse
    settings = keep_discovered_license(existing, settings)
    changed = existing is not None and existing != settings
    if changed and not force:
        raise CfError(f"{yaml_path} exists with other settings: "
                      + ", ".join(_differences(existing, settings)[:8]),
                      hint="pass --force to rewrite it; the project files init rendered are the "
                           "maintainer's and stay as they are", exit_code=1)
    repo = OutputRepo(paths, iid)
    _check_new_repo(repo, settings)
    result = Result(repo=repo.root)
    if existing is None or changed:
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        config.write(yaml_path, settings)
        result.yaml_written = True
    if ensure_workspace_ignores(workspace):
        result.notes.append(f"added the informalize lines to {paths.gitignore}")
    repo.init()
    for relative, raw in project.project_files(settings).items():
        if not (repo.root / relative).exists() and repo.write(relative, raw):
            result.written.append(relative)
    # LICENSE is the maintainer's once written, unless text.license itself changed
    license_raw = repo.read_bytes("LICENSE")
    license_changed = _text_license(existing) not in (None, settings["text"]["license"])
    if license_raw is None or (license_changed
                               and not project.license_matches(settings, license_raw)):
        if repo.write("LICENSE", project.license_text(settings)):
            result.written.append("LICENSE")
            if license_raw is not None:
                result.notes.append("LICENSE is now the text of " + settings["text"]["license"]
                                    + "; README.md and informal/main.tex name the license in "
                                      "words and are yours to edit")
    synced = runtime.sync(repo, settings)
    result.written += synced.written
    result.deleted += synced.deleted
    # the project files and LICENSE until their first commit (a later edit is the
    # maintainer's to commit), and the whole runtime
    tracked = repo.tracked()
    mine = [p for p in [*project.project_files(settings), "LICENSE"]
            if p not in tracked or p in result.written]
    title = settings["text"]["title"]
    result.commit = repo.commit(sorted(set(mine + synced.owned)),
                                f"Start the informal companion {iid}: {title}")
    result.head = repo.head()
    write_receipt(workspace, repo)
    return result


def sync(workspace, informalize_id: str) -> Result:
    """`cf informalize sync`: the runtime and informalize.json, committed, and the receipt."""
    cfg = config.load(workspace.paths, informalize_id, sessions=config.session_limit(workspace))
    repo = OutputRepo(workspace.paths, informalize_id)
    repo.require_stamp()
    synced = runtime.sync(repo, cfg)
    result = Result(repo=repo.root, written=synced.written, deleted=synced.deleted)
    result.commit = repo.commit(synced.owned,
                                f"Sync the informalize runtime ({len(synced.touched)} files)")
    result.head = repo.head()
    write_receipt(workspace, repo)
    return result


def sync_problems(workspace, informalize_id: str) -> list[str]:
    """`cf informalize sync --check`: read-only."""
    cfg = config.load(workspace.paths, informalize_id, sessions=config.session_limit(workspace))
    repo = OutputRepo(workspace.paths, informalize_id)
    repo.require_stamp()
    return runtime.problems(repo, cfg)


def report(result: Result) -> None:
    """Print what `init` or `sync` did."""
    log.ok(f"{result.repo}: {len(result.written)} written, {len(result.deleted)} deleted"
           + (f", committed {result.commit[:12]}" if result.commit else ", nothing to commit"))
    for note in result.notes:
        log.info(f"  {note}")
