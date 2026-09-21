"""cf doctor -- are this machine's prerequisites in place?

New code (skill-design#3).  The prerequisites were written down in README.md
prose only, and each one failed where it was needed: `pdftoppm not found` from
four levels inside a page render, `pdftotext` missing so a source silently had
no text and the quote gate could not run at all, `curl` missing so the errata
fetch died mid-onboard.  Every one of those is a first run on a machine nobody
has set up, and every one of them is one line to check up front.

    cf doctor
    cf doctor --json

Required: python (the floor `scripts/cf.py` enforces), git, pdftotext and
pdftoppm (poppler), curl.  Optional: node (only `cf plan-run`'s workflow scripts
parse with it), PyYAML (`cflib.yamlio` has a fallback reader) and git 2.31 or
later -- the one prerequisite that is a VERSION, because below it the findings
gate refuses to read a worktree naming a content filter it cannot blank
(`_git_version_row`).  Exit 0 when only optional things are missing, 2 when a
required one is.

It also prints the PDF search roots, because `workspace.yaml library_paths` plus
the roots derived from the workspace is both where `cf onboard` looks and the
allowlist for an absolute path a companion repo names in its own files.
"""
from __future__ import annotations

import json
import shutil
import sys

from cflib import gitsafe, log
from cflib.errors import CfError
from cflib.log import format_table


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "doctor", help="check this machine's prerequisites (and the PDF search roots)")
    p.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")


#: (name, what it is for, the one command that installs it).  The tools are the
#: four `shutil.which` gates in scripts/sources/*.py and cflib/discovery.py.
REQUIRED_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("git", "reading a companion repo's commit and tracked files",
     "install git (xcode-select --install, or your platform's package manager)"),
    ("pdftotext", "source text for items, the quote gate and the anchor check",
     "install poppler (brew install poppler / apt-get install poppler-utils)"),
    ("pdftoppm", "page images for `cf pair` crops",
     "install poppler (brew install poppler / apt-get install poppler-utils)"),
    ("curl", "`cf onboard --fetch-errata` and `cf onboard --download`",
     "install curl"),
)

OPTIONAL_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("node", "parsing the generated workflow scripts (tests skip without it)",
     "install node if you want the workflow scripts checked"),
)


def _git_version_row() -> dict:
    """``git --version`` beside the presence check, against the gate's floor.

    A version, not only a name on PATH, because one thing here needs a specific
    git: ``GIT_CONFIG_COUNT`` and its pairs arrived in 2.31, and they are the
    only channel that can blank a content-filter driver whose name carries an
    ``=`` (``git -c`` splits the argument at the first one).  Below that floor
    the findings gate REFUSES to read a worktree that names such a driver rather
    than run the program it points at, and ``cflib.gitsafe`` does the same on a
    companion tree -- so on an old run host the requirement was met for the first
    time as a blocked run with no warning anywhere before it
    (``references/runbook-server.md`` §2).

    A WARNING and not a failure: every other command works on an older git, and
    the machine running `cf doctor` is often not the machine running the gate.
    """
    version = gitsafe.git_version()
    floor = ".".join(str(n) for n in gitsafe.CONFIG_ENV_SINCE)
    return {
        "name": f"git {floor}+",
        "required": False,
        "ok": gitsafe.honours_config_env(),
        "detail": ".".join(str(n) for n in version) if version
                  else "`git --version` answered nothing this could read",
        "fix": f"install git {floor} or later on the run host: below it the "
               f"findings gate stops rather than read a worktree that names a "
               f"content filter whose own name carries an '='",
    }


def check() -> list[dict]:
    """One row per prerequisite: name, required, ok, detail, fix."""
    # imported here, not at module scope: cf.py imports every cfcmd module while
    # it is still initialising itself, and a command module must not depend on
    # how far through that it got.
    import cf as cf_main

    rows: list[dict] = [{
        "name": "python",
        "required": True,
        "ok": not cf_main.python_too_old(),
        "detail": "%d.%d.%d" % sys.version_info[:3],
        "fix": "run cf with Python %d.%d or newer" % cf_main.MIN_PYTHON,
    }]
    for name, why, fix in REQUIRED_TOOLS:
        found = shutil.which(name)
        rows.append({"name": name, "required": True, "ok": bool(found),
                     "detail": found or why, "fix": fix})
        if name == "git" and found:
            rows.append(_git_version_row())
    for name, why, fix in OPTIONAL_TOOLS:
        found = shutil.which(name)
        rows.append({"name": name, "required": False, "ok": bool(found),
                     "detail": found or why, "fix": fix})
    try:
        from cflib import yamlio
        have_pyyaml = bool(yamlio.HAVE_PYYAML)
    except Exception:  # pragma: no cover - cflib is importable or nothing works
        have_pyyaml = False
    rows.append({
        "name": "PyYAML",
        "required": False,
        "ok": have_pyyaml,
        "detail": "installed" if have_pyyaml
                  else "cflib.yamlio falls back to its own reader for the subset cf writes",
        "fix": "pip install pyyaml (optional; only widens what cf can read)",
    })
    return rows


def run(args) -> int:
    rows = check()
    missing_required = [r for r in rows if r["required"] and not r["ok"]]
    missing_optional = [r for r in rows if not r["required"] and not r["ok"]]
    roots = _search_roots(args)

    if args.as_json:
        print(json.dumps({"checks": rows, "pdf_search_roots": roots,
                          "ok": not missing_required}, indent=2))
        return 2 if missing_required else 0

    log.info(format_table(
        ["prerequisite", "", "status", "detail"],
        [[r["name"], "required" if r["required"] else "optional",
          "ok" if r["ok"] else "MISSING", r["detail"]] for r in rows]))
    log.info("")
    for row in missing_required + missing_optional:
        line = f"{row['name']} is missing: {row['fix']}"
        (log.warn if row["required"] else log.info)(line)
    if roots is not None:
        log.info("")
        log.info("pdf search roots (workspace.yaml library_paths + derived):")
        for root in roots:
            log.info(f"  {root}")
    if missing_required:
        log.info("")
        log.error("error: " + ", ".join(r["name"] for r in missing_required)
                  + " missing; cf cannot prepare a source without them")
        return 2
    if not missing_optional:
        log.ok("every prerequisite is present")
    return 0


def _search_roots(args) -> list[str] | None:
    """The workspace's PDF search roots, or None when there is no workspace here.

    `cf doctor` has to work before `cf init` does -- that is most of the point --
    so a missing or half-written workspace is not an error here.
    """
    try:
        from cflib.workspace import from_args
        return list(from_args(args, validate=False).library_paths)
    except CfError:
        return None
