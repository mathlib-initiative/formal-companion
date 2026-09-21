"""cf quotes hydrate -- put a redacted quote's text back, from your own source.

    cf quotes hydrate --bundle ../tao-share.tar.gz
    cf quotes hydrate --ledger --companion adam
    cf quotes hydrate --bundle ../tao-share --out sources/hydrated.adam.jsonl

A shared finding carries no book text: ``cf share export`` replaces every quote
with ``{page, length, sha256 of the normalised quote, and a few words from each
end -- at most a quarter of them, and none at all for a short sentence}``.
This command reverses that for somebody who holds the same edition, out of THEIR
OWN ``sources/<id>/text.txt``: it slides a window of the recorded length over the
recorded page (plus or minus the quote gate's ``PAGE_BIND_WINDOW``) and keeps the
passage whose sha256 matches.  The hash is the verification -- a recovered
sentence is the finding's sentence or the command says it could not find one.

Three outcomes, all counted: **hydrated**, **not-found** (this is not the edition
the finding was written against, or the quote was not copied out of the pinned
text) and **ambiguous** (two differently-spelled passages hash to the same quote).

A redacted ``book_quote`` is not the only thing to put back: ``cf share export``
also redacts a verbatim run of the source out of EVERY free-text field of a row,
at any depth, into the row's ``redacted_spans``, and each of those is hydrated
the same way, with the report's ``field`` naming the path it came from
(``reason``, ``witness.why``, ``dissent[0].argument``).

``--out`` refuses any path ``cf share export`` would put in a bundle, any path
inside a companion repo, and any path the surrounding git repo does not IGNORE,
so book text recovered here cannot end up in the one artifact whose purpose is to
travel, nor in an audited tree this skill only ever reads, nor in the history of
the repo the no-remote rule is about.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from cflib import gitsafe, log
from cflib.errors import CfError
from cflib.jsonio import read_jsonl, write_jsonl
from cflib.log import format_table
from cflib.paths import skill_root
from cflib.workspace import from_args
from share import bundle as bundle_mod
from share import hydrate as hydrate_mod

NAME = "quotes"
HELP = "recover a redacted quote's text from this machine's own copy of the source"


def register(subparsers) -> None:
    p = subparsers.add_parser(
        NAME, help=HELP, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="quotes_command", metavar="<subcommand>")

    hy = sub.add_parser("hydrate", help="recover redacted quotes (hash-verified)",
                        description=__doc__,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    hy.add_argument("--bundle", default="",
                    help="a bundle `cf share export` wrote (directory or .tar.gz): hydrate its "
                         "routed findings")
    hy.add_argument("--ledger", action="store_true",
                    help="hydrate the redacted quotes in THIS workspace's ledger/ instead "
                         "(the default when --bundle is not given)")
    hy.add_argument("--companion", action="append", default=[],
                    help="only this companion's rows (repeatable)")
    hy.add_argument("--out", default="",
                    help="write the hydrated rows as JSONL here (default: print them). "
                         "Refused inside a companion repo, for any path `cf share export` "
                         "would include in a bundle, and for a path the surrounding git "
                         "repo does not ignore")
    hy.add_argument("--no-text", action="store_true",
                    help="print the table and the counts only, not the recovered passages")


def run(args) -> int:
    command = getattr(args, "quotes_command", None)
    if not command:
        raise CfError("cf quotes needs a subcommand",
                      hint="`cf quotes hydrate --bundle <bundle>` recovers the text of a shared "
                           "campaign's quotes from this machine's own source")
    workspace = from_args(args)
    return _hydrate(workspace, args)


def _tracked_by_git(out: Path) -> Path | None:
    """The git repo that would TRACK *out*, or ``None``.

    ``git check-ignore`` is the only authority on this: the ignore rule may come
    from the workspace's own .gitignore, from an outer repo's, or from the user's
    global excludes, and a workspace created before `cf init` learned to ignore
    ``quotes/`` has no such rule at all.  A directory with no repo around it cannot
    track anything, so it is not refused.

    Both calls go through :mod:`cflib.gitsafe`, not plain ``git``: the repo being
    asked is whatever encloses the operator's ``--out``, which may be a tree this
    skill does not own, and ``check-ignore`` refreshes the index, which runs the
    ``core.fsmonitor`` command that repository's own config names (RULES: every
    git invocation on a repo you do not own goes through ``cflib.gitsafe``).  The
    safety check must not be the execution vector.
    """
    # `git -C` needs a directory that exists, and --out normally names a file in one
    # that does not yet: the check has to happen BEFORE the write.
    here = out
    while not here.is_dir() and here != here.parent:
        here = here.parent
    try:
        found = gitsafe.run(here, ["rev-parse", "--show-toplevel"])
        repo = Path(found.stdout.strip()) if found.returncode == 0 and found.stdout.strip() else None
        if not repo:
            return None
        ignored = gitsafe.run(repo, ["check-ignore", "-q", str(out)]).returncode == 0
    except subprocess.TimeoutExpired:
        # A git that hangs tells us nothing, and "nothing" here must mean "do not
        # write", so name the directory we were standing in.
        return here
    return None if ignored else repo


def _refuse_unsafe_out(out: Path, workspace) -> None:
    """``--out`` may not be a bundled file, a companion repo, the skill repo or tracked.

    The last of those is the one measured: `cf init` writes ``quotes/`` into a
    NEW workspace's .gitignore, and ``_write_if_absent`` leaves an existing
    .gitignore alone, so in every workspace created before that line existed
    ``quotes/hydrated.<companion>.jsonl`` -- the path this command's own help
    suggests -- is a tracked file holding recovered book text, in the repo the
    no-remote rule is about.  An ignore rule that is only present in new
    workspaces cannot be the guarantee, so the guarantee is this check.

    The companion check comes BEFORE it, and not only because a companion tree is
    never written to (CLAUDE.md S4): asking a companion repo whether it ignores a
    path runs git inside it, and a tree with no ``.git`` at all answers nothing,
    so a companion that happens to ignore the path -- or that is not a repo --
    would have been written to while the git check said the write was safe.
    ``share.bundle._refuse_unsafe_out`` already refuses this destination for
    ``cf share export --out``; this is the same loop for the same reason.
    """
    root = Path(workspace.root).resolve()
    relative = ""
    if out == root or root in out.parents:
        relative = out.relative_to(root).as_posix()
    if relative and bundle_mod.would_export(relative):
        raise CfError(
            f"refusing to write hydrated book text to {out}: `cf share export` puts that path "
            "in a bundle",
            hint="write it somewhere the export does not read, e.g. --out "
                 f"{root / 'sources' / out.name} (sources/ is gitignored), and keep the "
                 "recovered text on the machine that holds the source")
    if out == skill_root() or skill_root() in out.parents:
        raise CfError(
            f"refusing to write hydrated book text inside the skill repo ({skill_root()})",
            hint="the skill repo holds code, templates and docs only (CLAUDE.md S1)")
    for name, companion in sorted(workspace.companions.items()):
        if str(companion.repo) in ("", "."):   # an unset repo resolves to the cwd
            continue
        repo = Path(str(companion.repo)).expanduser().resolve()
        if out == repo or repo in out.parents:
            raise CfError(
                f"refusing to write hydrated book text inside the companion repo {name} "
                f"({repo})",
                hint="a companion tree is read as data and nothing this skill runs writes "
                     f"there (CLAUDE.md S4); write it under --out "
                     f"{root / 'sources' / out.name} instead (sources/ is ignored in every "
                     "workspace ever created)")
    repo = _tracked_by_git(out)
    if repo:
        raise CfError(
            f"refusing to write hydrated book text to {out}: {repo} does not ignore that path",
            hint=f"`cf init` ignores quotes/ in a workspace it creates, but it never rewrites "
                 f"an existing .gitignore -- write it under --out "
                 f"{root / 'sources' / out.name} instead (sources/ is ignored in every "
                 f"workspace ever created), or add the missing rule to "
                 f"{repo / '.gitignore'} yourself")


def _rows_from_bundle(path: str, companions: list[str]) -> tuple[list[dict], str]:
    """Every routed/ledger row of a bundle, with the bundle's own description."""
    with tempfile.TemporaryDirectory(prefix="cf-quotes-") as tmp:
        bundle = bundle_mod.open_bundle(path, Path(tmp) / "bundle")
        manifest = bundle_mod.read_manifest(bundle)
        problems = bundle_mod.verify_bundle(bundle, manifest)
        if problems:
            raise CfError(
                f"{path} does not match its {bundle_mod.MANIFEST_NAME}: "
                + "; ".join(problems[:4]),
                hint="the bundle was altered or truncated in transit; ask for it again")
        rows: list[dict] = []
        for file in bundle_mod.bundle_routed_files(bundle) + bundle_mod.bundle_ledger_files(bundle):
            name = bundle_mod.companion_of(file)
            if companions and name not in companions:
                continue
            rows.extend(r for r in read_jsonl(file)
                        if not (isinstance(r, dict) and "_meta" in r))
        return rows, (f"bundle {manifest.get('bundle_id', '')[:12]} from workspace "
                      f"{manifest.get('workspace')!r}")


def _rows_from_ledger(workspace, companions: list[str]) -> tuple[list[dict], str]:
    paths = workspace.paths
    names = companions or list(workspace.companion_names())
    rows: list[dict] = []
    for name in names:
        for path in (paths.routed_jsonl(name), paths.accepted_jsonl(name),
                     paths.refuted_jsonl(name)):
            if path.exists():
                rows.extend(r for r in read_jsonl(path)
                            if not (isinstance(r, dict) and "_meta" in r))
    return rows, f"this workspace's ledger ({', '.join(names) or 'no companions'})"


def _hydrate(workspace, args) -> int:
    if args.bundle and args.ledger:
        raise CfError("--bundle and --ledger name two different sets of rows",
                      hint="run the command twice, once with each")
    rows, where = (_rows_from_bundle(args.bundle, args.companion) if args.bundle
                   else _rows_from_ledger(workspace, args.companion))
    redacted = hydrate_mod.redacted_rows(rows)
    log.info(f"source    : {where}")
    if not redacted:
        log.info(f"{len(rows)} row(s) read, none with a redacted quote: nothing to hydrate")
        if not args.bundle:
            log.info("this workspace's own rows carry their quotes verbatim; a bundle somebody "
                     "sent you is the redacted case (`cf quotes hydrate --bundle <bundle>`)")
        return 0
    source_ids = sorted({str(r.get("source_id") or "") for r in redacted})
    texts = hydrate_mod.source_texts(workspace.paths, source_ids)
    reports, counts = hydrate_mod.hydrate_rows(redacted, texts)
    log.info(format_table(
        ["item", "kind", "field", "page", "status"],
        [[r["item"], r["kind"], r["field"], r["page"], r["status"]] for r in reports]))
    log.info(f"hydrated {counts[hydrate_mod.HYDRATED]}, "
             f"not-found {counts[hydrate_mod.NOT_FOUND]}, "
             f"ambiguous {counts[hydrate_mod.AMBIGUOUS]}"
             + (f", no source text {counts['no-source']}" if counts["no-source"] else "")
             + f"  (of {counts['redacted']} redacted quote(s))")
    for report in reports:
        if report["status"] != hydrate_mod.HYDRATED:
            log.warn(f"{report['item']} ({report['field']}): {report['status']} -- "
                     f"{report['why']}")
    if not args.no_text:
        for report in reports:
            if report["status"] == hydrate_mod.HYDRATED:
                log.info("")
                log.info(f"{report['item']} ({report['kind']}), {report['field']}, "
                         f"page {report['page']}:")
                log.info(f"  {report['text']}")
    if args.out:
        out = Path(args.out).expanduser().resolve()
        _refuse_unsafe_out(out, workspace)
        write_jsonl(out, reports, meta={
            "read_from": where,
            "counts": counts,
            "note": ("hydrated book text: recovered from this machine's own sources/<id>/"
                     "text.txt and verified by sha256. It is book text, so it stays here -- "
                     "`cf share export` never reads this file"),
        })
        log.ok(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    register(parser.add_subparsers())
    raise SystemExit(run(parser.parse_args()))
