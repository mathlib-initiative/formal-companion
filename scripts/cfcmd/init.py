"""cf init -- create a campaign directory for one book.

New code (design/architecture.md S2).  The campaign directory is the
~/Desktop/tao-differential role: a local git repo with NO remote holding the
book, the statement tables, the findings corpus and the ledgers.  The skill
repo holds code, templates and docs only; campaign data never enters it.

    cf init ~/Desktop/tao-differential --book tao-measure \\
        --pdf ~/Desktop/measure_theory/.lake/official-pdf/gsm-126-tao5-measure-book.pdf \\
        --offset 16 --errata-url https://example.org/errata
"""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from cflib import log
from cflib.campaign import CAMPAIGN_FILE, Campaign
from cflib.errors import CfError
from cflib.hashing import sha256_file, short
from cflib.items import DEFAULT_KINDS
from cflib.jsonio import atomic_write
from cflib.paths import CampaignPaths, skill_root
from cflib.templates import render_template

GITIGNORE = """\
# Written by `cf init`. The campaign directory is a LOCAL git repo with no remote.
#
# Copyrighted book material never enters git:
book/
**/pages/
**/images/
**/crops/
*.pdf

# Agent transcripts, tend logs and its sqlite copy quote the book verbatim:
runs/*/tend-root/
runs/*/logs/
**/sessions/

# Large derived artifacts:
detect/*/deps.json

# corpus/ is deliberately NOT ignored: the findings corpus is the calibration
# baseline and must be durable. It quotes 1-3 book sentences per finding, so the
# repo stays local (see the pre-push hook) and exports run with --redact-quotes.

__pycache__/
*.pyc
.DS_Store
"""

PRE_PUSH_HOOK = """\
#!/bin/sh
# Installed by `cf init`. This campaign repo holds copyrighted book quotes,
# private Lean statements and UNCHALLENGED findings; it is not meant to be
# pushed anywhere. Set CF_ALLOW_PUSH=1 for a deliberate, reviewed exception.
if [ "$CF_ALLOW_PUSH" != "1" ]; then
    echo "pre-push refused: campaign repositories have no remote by default." >&2
    echo "  book quotes + unchallenged findings live here; export instead" >&2
    echo "  (cf report --redact-quotes), or set CF_ALLOW_PUSH=1 to override." >&2
    exit 1
fi
exit 0
"""


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "init", help="create a campaign directory for one book",
        description=__doc__.split("\n\n")[1] if __doc__ else None)
    p.add_argument("directory", help="campaign directory to create (outside every repo)")
    p.add_argument("--book", required=True, metavar="ID",
                   help="book id, e.g. tao-measure (used as book/<id>/)")
    p.add_argument("--pdf", metavar="PATH", help="pinned PDF; only its sha256 is recorded")
    p.add_argument("--sha256", metavar="HEX", help="book sha256 when the PDF is not on this machine")
    p.add_argument("--offset", type=int, default=0, metavar="N",
                   help="printed page = PDF page - N (16 for Tao GSM 126)")
    p.add_argument("--errata-url", metavar="URL", help="live errata page (fetched with curl, never summarised)")
    p.add_argument("--title", default="", help="book title")
    p.add_argument("--edition", default="", help="edition label, e.g. 'GSM 126 (published)'")
    p.add_argument("--kinds", default=",".join(DEFAULT_KINDS),
                   help="comma-separated item kinds for the strict item grammar")
    p.add_argument("--chapters", default="",
                   help="comma-separated chapters under audit (default: the whole book); "
                        "every stage narrows to this, so the scope is decided once")
    p.add_argument("--name", help="campaign name (default: the book id)")
    p.add_argument("--no-git", action="store_true", help="do not run git init")
    p.add_argument("--allow-nested", action="store_true",
                   help="create the campaign inside an existing git repo anyway; the pre-push "
                        "hook and the ignore rules are then installed in THAT repo. Refused "
                        "outright when the enclosing repo has a remote")
    p.add_argument("--force", action="store_true", help="overwrite an existing campaign.yaml")


def run(args) -> int:
    root = Path(args.directory).expanduser().resolve()
    _refuse_unsafe_location(root)
    # BEFORE anything is written: the refusal is worthless once the campaign's
    # .gitignore and campaign.yaml are already untracked content of the outer repo.
    if not args.no_git:
        _refuse_nested_git(root, allow_nested=args.allow_nested)
    root.mkdir(parents=True, exist_ok=True)

    book_sha = args.sha256
    if args.pdf:
        pdf = Path(args.pdf).expanduser()
        if not pdf.exists():
            raise CfError(f"--pdf {pdf} does not exist",
                          hint="pin the edition later with `cf book` or pass --sha256")
        book_sha = sha256_file(pdf)
        log.detail(f"pdf sha256 {book_sha}")

    paths = CampaignPaths(root=root, book_id=args.book)
    made = paths.ensure_skeleton()
    for d in made:
        log.detail(f"created {d}")

    _write_if_absent(paths.gitignore, GITIGNORE, args.force)

    campaign_yaml = root / CAMPAIGN_FILE
    if campaign_yaml.exists() and not args.force:
        log.info(f"{CAMPAIGN_FILE} already exists; left untouched (use --force to rewrite)")
    else:
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
        chapters = [c.strip() for c in args.chapters.split(",") if c.strip()]
        text = render_template("campaign.yaml.example", {
            "campaign_name": args.name or args.book,
            "created": _quote(date.today().isoformat()),
            "book_id": args.book,
            "book_title": _quote(args.title or args.book),
            "edition": _quote(args.edition or "unspecified"),
            "pdf_path": str(Path(args.pdf).expanduser()) if args.pdf else "null",
            "book_sha256": _quote(book_sha) if book_sha else "null",
            "offset": args.offset,
            "errata_url": args.errata_url or "null",
            "kinds": "[" + ", ".join(kinds) + "]",
            "chapters": "[" + ", ".join(chapters) + "]",
        })
        atomic_write(campaign_yaml, text)
        log.ok(f"wrote {campaign_yaml}")

    campaign = Campaign.load(root)  # validates what we just wrote
    if not args.no_git:
        _init_git(root, allow_nested=args.allow_nested,
                  allow_remote=bool(campaign.policy.get("allow_remote", False)))
    log.info("")
    log.info(f"campaign : {campaign.root}")
    log.info(f"book     : {campaign.book_id}"
             + (f"  sha {short(campaign.book_sha)}" if campaign.book_sha else "  (edition not pinned)")
             + f"  offset {campaign.offset}")
    log.info(f"kinds    : {', '.join(campaign.kinds)}")
    log.info("")
    log.info("next:")
    log.info(f"  cf --campaign {root} book {campaign.book_id}        # text, pages, items.json, errata")
    log.info(f"  cf --campaign {root} onboard <name> --repo <path> --adapter docstring_tags")
    if not campaign.book_sha:
        log.warn("no book sha pinned: every quote gate and edition gate needs one")
    return 0


def _quote(text: str) -> str:
    return '"' + text.replace('"', '\\"') + '"'


def _write_if_absent(path: Path, text: str, force: bool) -> None:
    if path.exists() and not force:
        log.detail(f"{path} exists; left untouched")
        return
    atomic_write(path, text)
    log.ok(f"wrote {path}")


def _refuse_unsafe_location(root: Path) -> None:
    """Campaign data must not land inside the skill repo or a companion repo."""
    skill = skill_root()
    if root == skill or skill in root.parents:
        raise CfError(f"refusing to create a campaign inside the skill repo ({skill})",
                      hint="the skill repo holds code, templates and docs only; "
                           "put the campaign somewhere like ~/Desktop/<book>-campaign")
    if (root / ".lake").exists() or (root / "lakefile.toml").exists() or (root / "lakefile.lean").exists():
        raise CfError(f"{root} looks like a Lean companion repo",
                      hint="the campaign directory lives outside every companion repo")


def _nearest_existing(path: Path) -> Path:
    """The closest ancestor of *path* that exists (git needs a real -C)."""
    cur = path
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    return cur


def _refuse_nested_git(root: Path, *, allow_nested: bool) -> None:
    """Refuse a campaign nested inside an existing repository.

    `cf init` refuses the skill repo and a Lean companion, but a directory
    *inside* some other repository only warned "not running git init" and exited
    0.  No pre-push hook was installed anywhere -- the campaign had no .git of
    its own and the enclosing repo was untouched -- so corpus/ (1-3 verbatim book
    sentences per finding), ledger/, companions/*/statements.jsonl and
    runs/*/tasks/ became ordinary untracked content of a repo that may have a
    public remote.
    """
    if (root / ".git").exists():
        return
    top = _git_toplevel(_nearest_existing(root))
    if not top or top == root:
        return
    remotes = _remotes(top)
    if remotes:
        raise CfError(
            f"{root} is inside the git repo {top}, which has a remote ({remotes[0]})",
            hint="a campaign holds verbatim book quotes and UNCHALLENGED findings; put it "
                 "outside that repo. --allow-nested does NOT override this.")
    if not allow_nested:
        raise CfError(
            f"{root} is inside the git repo {top}",
            hint="campaign repos are standalone and have no remote; put the campaign outside "
                 "that repo, or pass --allow-nested to install the pre-push guard and the "
                 "campaign's ignore rules in the enclosing repo instead")


def _remotes(repo: Path) -> list[str]:
    res = subprocess.run(["git", "-C", str(repo), "remote"], capture_output=True, text=True)
    return res.stdout.split() if res.returncode == 0 else []


def _install_hook(repo: Path) -> None:
    """Install the pre-push guard in *repo* (never overwrites an existing hook)."""
    hook = repo / ".git" / "hooks" / "pre-push"
    if not hook.parent.is_dir():
        log.warn(f"{hook.parent} does not exist; no pre-push guard installed")
        return
    if hook.exists():
        log.detail(f"{hook} exists; left untouched")
        return
    atomic_write(hook, PRE_PUSH_HOOK)
    hook.chmod(0o755)
    log.ok(f"installed {hook}")


def _init_git(root: Path, allow_nested: bool = False, allow_remote: bool = False) -> None:
    """git init with no remote; install the pre-push guard.

    The nested case -- a campaign created INSIDE an existing repository -- used
    to warn and return, leaving no hook anywhere and no .git of its own.  The
    campaign's deliberately-not-ignored content (corpus/, ledger/,
    companions/*/statements.jsonl, runs/*/tasks/) then became ordinary untracked
    content of the outer repo, which may have a public remote, and the "campaign
    repos have no remote" invariant the whole copyright story rests on was
    silently never established.
    """
    git_dir = root / ".git"
    if not git_dir.exists():
        top = _git_toplevel(root)
        if top and top != root:
            # _refuse_nested_git() already refused the cases that must not happen.
            log.warn(f"{root} is inside {top}: guarding THAT repo instead of running git init")
            _install_hook(top)
            _append_ignore(top, root)
            return
        res = subprocess.run(["git", "init", "-q", str(root)], capture_output=True, text=True)
        if res.returncode != 0:
            log.warn(f"git init failed: {res.stderr.strip()}")
            return
        log.ok(f"git init {root} (no remote)")
    else:
        log.detail("git repo already present")
    remotes = _remotes(root)
    if remotes and not allow_remote:
        # campaign.yaml policy.allow_remote (false by default) is the rule; the
        # hook is the enforcement. Both, or the policy block is decoration.
        raise CfError(
            f"this campaign repo has a remote ({remotes[0]}) and campaign.yaml sets "
            "policy.allow_remote: false",
            hint="book quotes and UNCHALLENGED findings live here; run "
                 f"`git -C {root} remote remove {remotes[0]}`, or set policy.allow_remote: true "
                 "in campaign.yaml and take responsibility for every push")
    if remotes:
        log.warn(f"this campaign repo has a remote ({remotes[0]}) and policy.allow_remote is "
                 "true: book quotes and unchallenged findings must not be pushed")
    _install_hook(root)


def _append_ignore(outer: Path, campaign: Path) -> None:
    """Ignore the nested campaign from the enclosing repo's .gitignore."""
    rel = campaign.relative_to(outer).as_posix().rstrip("/")
    marker = f"/{rel}/"
    path = outer / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in existing.splitlines():
        log.detail(f"{path} already ignores {marker}")
        return
    block = (("" if existing.endswith("\n") or not existing else "\n")
             + f"\n# Added by `cf init --allow-nested`: campaign data (verbatim book quotes,\n"
               f"# UNCHALLENGED findings) must never be committed or pushed from this repo.\n"
               f"{marker}\n")
    path.write_text(existing + block, encoding="utf-8")
    log.ok(f"ignored {marker} in {path}")


def _git_toplevel(path: Path) -> Path | None:
    res = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return Path(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else None
