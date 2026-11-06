"""The canonical PDF, stage I6: `cf informalize pdf-command` and `fetch-pdf`.

New code, for docs/proposals/informalize.md section 9.  The two ends on the operator's
machine of a build that happens on the host, in the shape stage I1 already has
(`informalize.library`):

* **pdf-command** writes `informalize/<id>/pdf/<commit12>/to-host/src/` -- the output
  repository's files at HEAD, read from git's objects through `cflib.gitsafe`, never the
  worktree's -- and `pdf-command.sh`, the lines that copy that tree to
  `<remote_root>/pdf/<commit12>/src/` and start `informal/tools/canonical_pdf.sh` there,
  detached.  It runs nothing and reaches no host: the canonical PDF is the host's build,
  and building it is the operator's act.
* **fetch-pdf** asks the host whether the job wrote `DONE`, copies the PDF, its markers,
  its receipt and the proof sheets into `pdf/<commit12>/.incoming/`, checks them there
  (:func:`check_fetched`: the receipt is of this commit and this PDF, the markers are of
  that PDF, each of the script's checks passed), and only then puts them in place.  It
  copies `markers.json` into the output repository and commits it, and writes the I6
  receipt, which `cf informalize approve <id> proof-sheets` pins.

ssh and rsync run only under `--execute --i-have-consent`, against a host of
`hosts.allowlist`, the same exception CLAUDE.md section 3 allows `fetch-library`.
"""
from __future__ import annotations

import getpass
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cflib.errors import CfError
from cflib.hashing import sha256_file

from informalize import apply as apply_mod
from informalize import config, library, runtime, stages
from informalize.outrepo import OutputRepo
from informalize.plan import tree_at, write_files

#: what pdf-command writes beside the tree it copies: the lines it printed
COMMAND_FILE = "pdf-command.sh"
#: the runtime's script the host runs, as a path inside the copied tree
HOST_SCRIPT = "informal/tools/canonical_pdf.sh"
#: the canonical build's directory under the host's work folder, and the proof sheets in it
CANONICAL_BUILD = "a"
PROOF_SHEETS = "proof-sheets"
RECEIPT_NAME = "pdf-receipt.json"
MARKERS = "informal/markers.json"
PDF_SCHEMA = "formal-companion/informalize-pdf"
MARKERS_SCHEMA = "formal-companion/informalize-markers"
#: every check canonical_pdf.sh records, each of which must say `ok`
CHECKS = ("two_builds", "extract_markers", "check_items", "proof_sheets")
INCOMING = ".incoming"


def head_commit(repo: OutputRepo) -> str:
    """The output repository's HEAD, which the canonical PDF is of."""
    repo.require_stamp()
    head = repo.head()
    if head is None:
        raise CfError(f"{repo.root} has no commit yet",
                      hint="the canonical PDF is a build of a commit; run the writing phases "
                           "and `cf informalize apply` first")
    return head


def require_clean(repo: OutputRepo, why: str = "the canonical PDF is built from a commit, "
                                               "and every annotation pins the PDF it makes"
                  ) -> None:
    """Refuse a repository with work in it: stages I6 and I7 both act on a commit."""
    changes = repo.changes()
    if changes:
        raise CfError(f"{repo.root} has {len(changes)} uncommitted change(s), the first "
                      f"{changes[0]}", hint=f"{why}; commit or discard them first")


def _planned(paths, informalize_id: str, commit: str) -> bool:
    """Did `pdf-command` write the tree of this commit?"""
    return (paths.informalize_pdf_to_host(informalize_id, commit[:12]) / "src").is_dir()


def built_commit(repo: OutputRepo, paths, informalize_id: str) -> str:
    """The commit the canonical PDF is of: HEAD, or the commit before it when the only
    thing between them is the `informal/markers.json` a previous fetch committed.

    That commit is the one `pdf-command` planned, and the PDF stays its build: the
    exporter holds `informal/` at HEAD to `informal/` at the markers' `source_commit`
    except `markers.json` itself (section 10), and so does this.  Without it a second
    `fetch-pdf` would look for a work folder of a commit the host never built.
    """
    head = head_commit(repo)
    if _planned(paths, informalize_id, head):
        return head
    parent = repo.git("rev-parse", "--verify", "-q", f"{head}^", check=False).strip()
    if len(parent) == 40 and _planned(paths, informalize_id, parent):
        changed = [p for p in repo.git("diff", "--name-only", "-z", parent, head).split("\0")
                   if p]
        if changed == [MARKERS]:
            return parent
    raise CfError(f"nothing here plans a canonical PDF of {head[:12]}",
                  hint=f"`cf informalize pdf-command {informalize_id} --host <host>` writes "
                       "the tree the host builds and prints the lines that build it")


def _ready(workspace, informalize_id: str) -> None:
    """Refuse an id with a harvested phase nobody applied: the PDF would be of the text
    before it."""
    waiting = apply_mod.unapplied(workspace.paths, informalize_id)
    if waiting:
        raise CfError(f"{waiting[0]} is harvested and not applied",
                      hint=f"`cf informalize apply {informalize_id} --run {waiting[0]}` first; "
                           "the canonical PDF is of the text in the repository")


def _check_home(workspace, informalize_id: str, commit12: str) -> Path:
    """`informalize/<id>/pdf/<commit12>/`, refusing a link or a directory elsewhere."""
    paths = workspace.paths
    home = paths.informalize_dir(informalize_id)
    root = paths.informalize_pdf_root(informalize_id)
    here = paths.informalize_pdf(informalize_id, commit12)
    top = os.path.realpath(paths.root)
    for path in (home, root, here, here / "to-host", here / INCOMING):
        if path.is_symlink():
            raise CfError(f"{path} is a symlink", hint="it is written in place; remove the link")
    if not os.path.realpath(home).startswith(top + os.sep):
        raise CfError(f"{home} resolves outside the workspace {paths.root}")
    here.mkdir(parents=True, exist_ok=True)
    return here


# ---------------------------------------------------------------------------
# pdf-command
# ---------------------------------------------------------------------------

def pdf_command(workspace, informalize_id: str, host_name: str) -> library.BuildCommand:
    """Write `pdf/<commit12>/to-host/` and return the lines that run I6 on the host."""
    paths = workspace.paths
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    host, remote = library.host_folder(workspace, host_name, informalize_id)
    repo = OutputRepo(paths, informalize_id)
    head = head_commit(repo)
    require_clean(repo)
    _ready(workspace, informalize_id)
    problems = runtime.problems(repo, cfg)
    if problems:
        raise CfError(f"the output repository's runtime is not this checkout's: {problems[0]}",
                      hint=f"`cf informalize sync {informalize_id}` rewrites it; the host "
                           "builds with the runtime it holds")
    here = _check_home(workspace, informalize_id, head[:12])
    to_host = paths.informalize_pdf_to_host(informalize_id, head[:12])
    write_files(tree_at(repo.root, head), to_host / "src")
    lines = _command_lines(cfg, host, remote, to_host, head)
    (to_host / COMMAND_FILE).write_text("#!/bin/sh\nset -eu\n" + "\n".join(lines) + "\n",
                                        encoding="utf-8")
    files = {p.relative_to(to_host).as_posix(): sha256_file(p)
             for p in sorted((to_host / "src").rglob("*")) if p.is_file()}
    return library.BuildCommand(to_host=here, lines=lines, files=files)


def _command_lines(cfg: dict, host: dict, remote: library.RemoteRoot, to_host: Path,
                   head: str) -> list[str]:
    ssh = shlex.quote(host["ssh"])
    short = head[:12]
    work = remote.shell(f"pdf/{short}")
    start = (f"cd {work}/src && setsid -f bash {HOST_SCRIPT} --source-commit {head} {work} "
             f">> {work}/canonical_pdf.log 2>&1 < /dev/null")
    watch = f"tail -n 20 {work}/canonical_pdf.log; cat {work}/DONE"
    return [
        f"# Stage I6 of {cfg['id']} (docs/proposals/informalize.md section 9): on "
        f"{host['name']},",
        f"# build the canonical PDF of the output repository at {short} twice from clean,",
        "# compare the two sha256, extract the markers, run check_items --strict and draw",
        "# the proof sheets. Written by `cf informalize pdf-command`, which runs nothing:",
        "# run these lines on this machine, as the operator. The PDF the annotations pin is",
        "# the host's build; a rebuild elsewhere is a different PDF (section 14, risk 1).",
        f"ssh -n {ssh} {shlex.quote('mkdir -p ' + work + '/src')}",
        f"rsync -a --delete {shlex.quote(str(to_host / 'src'))}/ {ssh}:{work}/src/",
        f"ssh -n {ssh} {shlex.quote(start)}",
        "# The job writes DONE, its exit status, last. To watch it:",
        f"#   ssh -n {ssh} {shlex.quote(watch)}",
        "# When DONE holds 0, bring the PDF, its markers, its receipt and the proof sheets",
        "# home:",
        f"#   cf informalize fetch-pdf {cfg['id']} --host {host['name']} --execute "
        "--i-have-consent",
        "# Once it is fetched the builds may go; the PDF is at home:",
        f"#   ssh -n {ssh} {shlex.quote('rm -rf ' + work)}",
    ]


# ---------------------------------------------------------------------------
# fetch-pdf
# ---------------------------------------------------------------------------

def fetch_commands(cfg: dict, host: dict, remote: library.RemoteRoot, commit12: str,
                   incoming: Path) -> list[list[str]]:
    """The ssh test of DONE, then the copies, in the order fetch-pdf runs them."""
    ssh = host["ssh"]
    work = remote.rsync(f"pdf/{commit12}")
    build = f"{work}/{CANONICAL_BUILD}"
    job = cfg["text"]["job"]
    return [
        ["ssh", "-n", ssh, f"test -f {remote.shell(f'pdf/{commit12}/DONE')}"],
        ["rsync", "-a", "--no-links", f"{ssh}:{work}/DONE", str(incoming / "DONE")],
        ["rsync", "-a", "--no-links", f"{ssh}:{work}/{RECEIPT_NAME}",
         str(incoming / RECEIPT_NAME)],
        ["rsync", "-a", "--no-links", f"{ssh}:{build}/{job}.pdf", str(incoming / f"{job}.pdf")],
        ["rsync", "-a", "--no-links", f"{ssh}:{build}/markers.json",
         str(incoming / "markers.json")],
        ["rsync", "-a", "--no-links", "--delete", f"{ssh}:{build}/{PROOF_SHEETS}/",
         f"{incoming / PROOF_SHEETS}/"],
    ]


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    """One ssh or rsync call (the tests replace this)."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=3600)


def _json(path: Path, what: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise CfError(f"no {what} ({path})",
                      hint="the host job did not write it; read its canonical_pdf.log")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CfError(f"{what} ({path}) is not JSON: {exc}") from None


def check_done(incoming: Path, commit12: str) -> None:
    """Refuse a job that did not end with 0, naming its log."""
    path = incoming / "DONE"
    if path.is_symlink() or not path.is_file():
        raise CfError(f"no DONE in {incoming}", hint="the host job has not finished")
    done = path.read_text(encoding="utf-8", errors="replace").strip()
    if done != "0":
        why = {"3": "a program or a TeX file is missing", "4": "poppler is not on the host",
               "5": "the two clean builds differ", "6": "a check of the build failed",
               "7": "the receipt could not be written"}.get(done, "it failed")
        raise CfError(f"the host job ended with status {done!r}, not 0: {why}",
                      hint=f"read pdf/{commit12}/canonical_pdf.log on the host (and the "
                           "step's own log beside it), then run the printed lines of "
                           "pdf-command again")


def check_fetched(incoming: Path, cfg: dict, head: str) -> dict:
    """Every check section 9 makes of a fetched PDF; the facts the I6 receipt records."""
    job = cfg["text"]["job"]
    check_done(incoming, head[:12])
    problems: list[str] = []
    receipt = _json(incoming / RECEIPT_NAME, RECEIPT_NAME)
    receipt = receipt if isinstance(receipt, dict) else {}
    if receipt.get("schema") != PDF_SCHEMA or receipt.get("schema_version") != 1:
        problems.append(f"{RECEIPT_NAME} is not a {PDF_SCHEMA} receipt of version 1")
    if receipt.get("job") != job:
        problems.append(f"{RECEIPT_NAME} is the receipt of job {receipt.get('job')!r}, not {job}")
    if receipt.get("source_commit") != head:
        problems.append(f"the PDF was built from {str(receipt.get('source_commit'))[:12]!r}, "
                        f"not the output repository's HEAD {head[:12]}")
    failed = sorted(name for name in CHECKS if (receipt.get("checks") or {}).get(name) != "ok")
    if failed:
        problems.append(f"the host's checks did not all pass: {', '.join(failed)}")

    pdf = incoming / f"{job}.pdf"
    if pdf.is_symlink() or not pdf.is_file():
        raise CfError(f"no {job}.pdf came back ({pdf})", hint="read the host's canonical_pdf.log")
    sha, size = sha256_file(pdf), pdf.stat().st_size
    stated = receipt.get("pdf") if isinstance(receipt.get("pdf"), dict) else {}
    if stated.get("sha256") != sha or stated.get("bytes") != size:
        problems.append(f"the PDF that came back is {sha[:12]} of {size} bytes; "
                        f"{RECEIPT_NAME} says {str(stated.get('sha256'))[:12]} of "
                        f"{stated.get('bytes')} bytes")
    if receipt.get("second_build_sha256") not in (None, sha):
        problems.append("the receipt's second build is not the PDF that came back")

    markers = _json(incoming / "markers.json", "markers.json")
    markers = markers if isinstance(markers, dict) else {}
    if markers.get("schema") != MARKERS_SCHEMA or markers.get("schema_version") != 1:
        problems.append(f"markers.json is not a {MARKERS_SCHEMA} document of version 1")
    if markers.get("job") != job:
        problems.append(f"markers.json is of job {markers.get('job')!r}, not {job}")
    if markers.get("pdf_sha256") != sha:
        problems.append(f"markers.json is of the PDF {str(markers.get('pdf_sha256'))[:12]}, "
                        f"not the one that came back ({sha[:12]})")
    if markers.get("source_commit") != head:
        problems.append(f"markers.json is of commit {str(markers.get('source_commit'))[:12]!r}, "
                        f"not {head[:12]}")
    markers_sha = sha256_file(incoming / "markers.json")
    if receipt.get("markers_sha256") not in (None, markers_sha):
        problems.append(f"{RECEIPT_NAME} pins another markers.json than the one that came back")

    sheets = [name for name in (receipt.get("proof_sheets") or []) if isinstance(name, str)]
    gone = [name for name in sheets if not (incoming / PROOF_SHEETS / name).is_file()]
    if gone:
        problems.append(f"{len(gone)} proof sheet(s) the receipt lists did not come back: "
                        + ", ".join(gone[:5]))
    if not sheets:
        problems.append("the receipt lists no proof sheet, and the maintainer approves them")
    if problems:
        raise CfError(f"the fetched PDF fails {len(problems)} check(s): {problems[0]}",
                      hint="; ".join(problems[1:5]) or
                           f"the fetched files stay in {incoming} for a look")
    return {"source_commit": head, "job": job,
            "pdf": {"sha256": sha, "bytes": size, "pages": stated.get("pages")},
            "markers_sha256": markers_sha, "proof_sheets": sorted(sheets),
            "checks": dict(sorted((receipt.get("checks") or {}).items())),
            "pdftex": receipt.get("pdftex"), "latexmk": receipt.get("latexmk"),
            "poppler": receipt.get("poppler"),
            "inputs": len(receipt.get("inputs") or {})}


@dataclass
class FetchPdfResult:
    """What fetch-pdf did."""

    commands: list[list[str]]
    commit: str = ""
    directory: Path | None = None
    facts: dict = field(default_factory=dict)
    receipt: Path | None = None
    committed: str | None = None


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def fetch_pdf(workspace, informalize_id: str, host_name: str, *, execute: bool, consent: bool,
              runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None
              ) -> FetchPdfResult:
    """Fetch I6 from the host, check it, put it in place, commit markers.json, write I6."""
    paths = workspace.paths
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    host, remote = library.host_folder(workspace, host_name, informalize_id)
    repo = OutputRepo(paths, informalize_id)
    head = built_commit(repo, paths, informalize_id)
    here = _check_home(workspace, informalize_id, head[:12])
    incoming = here / INCOMING
    commands = fetch_commands(cfg, host, remote, head[:12], incoming)
    result = FetchPdfResult(commands=commands, commit=head, directory=here)
    if not execute:
        return result
    if not consent:
        raise CfError("refusing to fetch from the host without --i-have-consent",
                      hint="the canonical PDF is built on a host you were allowed to use; say "
                           "so with --i-have-consent (recorded in the I6 receipt)")
    require_clean(repo)
    run = runner or _run
    probe, *copies = commands
    asked = run(probe)
    if asked.returncode == 1:
        raise CfError(f"{host['name']} has no pdf/{head[:12]}/DONE yet: the job is running, or "
                      "never started",
                      hint=f"`cf informalize pdf-command {informalize_id} --host "
                           f"{host['name']}` prints the lines that start it and the one that "
                           "watches it")
    if asked.returncode != 0:
        raise CfError(f"could not ask {host['name']} for DONE (ssh exited {asked.returncode}): "
                      f"{(asked.stderr or '').strip()[:200]}")
    if incoming.exists():
        _remove(incoming)
    incoming.mkdir(parents=True)
    for number, argv in enumerate(copies):
        done = run(argv)
        if done.returncode != 0:
            raise CfError(f"{argv[0]} exited {done.returncode}: "
                          f"{(done.stderr or done.stdout or '').strip()[:300]}",
                          hint=f"the command was: {' '.join(argv)}")
        if number == 0:
            # DONE came first: a job that failed left no PDF or receipt to copy
            check_done(incoming, head[:12])
    facts = check_fetched(incoming, cfg, head)
    job = cfg["text"]["job"]
    for name in (f"{job}.pdf", "markers.json", RECEIPT_NAME, PROOF_SHEETS):
        target = here / name
        if target.exists() or target.is_symlink():
            _remove(target)
        os.replace(incoming / name, target)
    _remove(incoming)
    repo.write(MARKERS, (here / "markers.json").read_bytes())
    result.committed = repo.commit([MARKERS],
                                   f"Record the canonical PDF's boxes (I6, {head[:12]})")
    facts["consent"] = {"i_have_consent": True, "operator": getpass.getuser(),
                        "host": host["name"]}
    result.facts = facts
    result.receipt = _write_receipt(paths, informalize_id, head, cfg, facts)
    return result


def _write_receipt(paths, informalize_id: str, head: str, cfg: dict, facts: dict) -> Path:
    """The I6 receipt: what the maintainer's proof-sheets approval pins."""
    home = paths.informalize_dir(informalize_id)
    here = paths.informalize_pdf(informalize_id, head[:12])
    prefix = here.relative_to(home).as_posix()
    job = cfg["text"]["job"]
    inputs = {"informalize.yaml": sha256_file(home / "informalize.yaml")}
    outputs = {f"{prefix}/{job}.pdf": sha256_file(here / f"{job}.pdf"),
               f"{prefix}/markers.json": sha256_file(here / "markers.json"),
               f"{prefix}/{RECEIPT_NAME}": sha256_file(here / RECEIPT_NAME),
               f"repo/{MARKERS}": sha256_file(here / "markers.json")}
    for name in facts["proof_sheets"]:
        outputs[f"{prefix}/{PROOF_SHEETS}/{name}"] = sha256_file(here / PROOF_SHEETS / name)
    return stages.write_receipt(paths, informalize_id, "I6", inputs=inputs, outputs=outputs,
                                checks=facts)


def current_receipt(paths, informalize_id: str) -> dict:
    """The I6 receipt, refused when it is missing or a file it pins has changed since."""
    receipt = stages.read_receipt(paths, informalize_id, "I6")
    if receipt is None:
        raise CfError(f"stage I6 has not run for {informalize_id}: no stages/I6.json",
                      hint=f"`cf informalize pdf-command {informalize_id} --host <host>`, then "
                           f"`cf informalize fetch-pdf {informalize_id} --host <host> --execute "
                           "--i-have-consent`")
    moved = stages.stale_pins(paths.informalize_dir(informalize_id), receipt["outputs"])
    if moved:
        raise CfError(f"the canonical PDF changed since stage I6 fetched it: {moved[0]}",
                      hint=f"`cf informalize fetch-pdf {informalize_id} --host <host> --execute "
                           "--i-have-consent` fetches and checks it again")
    return receipt
