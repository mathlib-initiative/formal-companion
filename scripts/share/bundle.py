"""Write and read a share bundle: what may leave the workspace directory.

New code.  ``cf share export`` collects the four artifact families that carry a
campaign's conclusions -- ledger rows, routed findings, tallies and run receipts
-- plus source METADATA, and writes them beside a ``MANIFEST.json`` that names
every file, its sha256 and the redaction mode.  What it does NOT collect is the
point of the command, and :data:`WITHHELD` is the list, checked by a test:

* no book text: ``sources/<id>/text.txt``, ``pages/``, ``images/``, ``crops/``,
  no PDF, and no corpus findings document (a findings file quotes the book; the
  routed file carries the same rows with the quote REDACTED by default);
* no private Lean statements (``companions/*/statements.jsonl``) unless
  ``--include-statements`` says so in as many words;
* no machine-local facts: a receipt's ``destination`` and its ``source.path`` /
  ``source.host`` are dropped, and a source's ``pdf`` path and a companion's
  ``repo`` path never travel.

The bundle is meant to be handed to a second person -- committed to a repo that
HAS a remote, mailed, carried on a stick.  That is why the command refuses to
write it inside the workspace repo or a companion repo (where it would be
swallowed by the very repositories the no-remote rule is about), and why the
quotes are redacted unless the operator asks for them.
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from cflib import log
from cflib.errors import CfError
from cflib.hashing import sha256_file, sha256_json, short
from cflib.jsonio import read_json, read_jsonl, write_json, write_jsonl
from cflib.paths import WorkspacePaths, skill_root

from . import redact as redact_mod
from .hydrate import locate_pages

#: the bundle format, in the manifest and checked on import
BUNDLE_SCHEMA = "cf-share-v1"
MANIFEST_NAME = "MANIFEST.json"
SOURCES_NAME = "sources.json"
#: suffixes that mean "write one file, not a directory"
TAR_SUFFIXES = (".tar.gz", ".tgz")

#: source-record keys that travel.  Facts ABOUT an edition -- the registry's own
#: rule (registry/README.md): shas, offsets, page maps, licence notes, never a
#: line of the text and never a path on the exporter's machine.
SOURCE_KEYS = (
    "sha256", "title", "authors", "edition", "isbn", "kinds", "offset",
    "offset_confidence", "offset_method", "pages", "text_sha256", "bytes",
    "errata_url", "chapters", "scope_method", "role", "same_work", "page_map_to",
    "confirmed", "registry", "licence",
)
#: companion keys that travel: what it is audited against, never where it lives
COMPANION_KEYS = ("adapter", "sources", "anchor_source", "bundled_sources", "commit")
#: receipt keys that travel (``destination`` and ``source.path``/``host`` do not)
RECEIPT_KEYS = (
    "run_id", "harvested_at", "sources", "expected", "actual", "missing", "extra",
    "sha_mismatched", "sha_verified", "malformed", "complete", "manifest_algo",
    "manifest", "edition", "by_source", "gate", "previous", "expected_from",
)
#: keys of a receipt's ``source`` block that travel
RECEIPT_SOURCE_KEYS = ("kind", "mode", "preset", "converted", "already_v2", "repos",
                       "repo_map", "repos_not_in_workspace")

#: what a bundle never carries, in the words the manifest records
WITHHELD = (
    "the pinned source text (sources/<id>/text.txt)",
    "page images and crops (sources/<id>/pages, images, crops)",
    "the source file itself (no PDF, no manuscript)",
    "the corpus findings documents (corpus/<run>/*.json quote the book; the routed "
    "rows carry the same findings with the quote redacted)",
    "the workspace's own paths (a receipt's destination and source.path/source.host)",
    "each source's local pdf path and each companion's repo path",
)


def is_tarball(out: Path) -> bool:
    return str(out).endswith(TAR_SUFFIXES)


def would_export(relative: str) -> bool:
    """Is *relative* a path ``cf share export`` can put into a bundle?

    ``cf quotes hydrate --out`` refuses these, so hydrated book text cannot be
    written into the one thing whose whole purpose is to travel.  One predicate,
    used by both commands, so the two cannot drift apart -- including the
    statements file, which only ``--include-statements`` exports but which is the
    last place recovered book text should land.
    """
    parts = Path(str(relative)).as_posix().strip("/").split("/")
    if not parts or not parts[0]:
        return False
    head, tail = parts[0], parts[-1]
    if tail in (MANIFEST_NAME, SOURCES_NAME):
        return True
    if head == "ledger" and tail.endswith(".jsonl"):
        return True
    if head == "reports" and tail.startswith("tally.") and tail.endswith(".json"):
        return True
    if head == "corpus" and tail == "RECEIPT.json":
        return True
    if head == "companions" and tail == "statements.jsonl":
        return True
    return False


def _pick(data: Any, keys: Sequence[str]) -> dict:
    """The allowlisted keys of a mapping, in the order *keys* names them."""
    data = dict(data or {})
    return {k: data[k] for k in keys if k in data and data[k] not in (None, "")}


def source_metadata(workspace: Any) -> dict:
    """The source and companion METADATA a bundle carries.

    Built from ``workspace.yaml``'s own blocks through :data:`SOURCE_KEYS` and
    :data:`COMPANION_KEYS`: an allowlist, not a blocklist, so a key added to
    ``workspace.yaml`` later cannot leak a path by default.
    """
    raw_sources = dict((workspace.data.get("sources") or {}))
    raw_companions = dict((workspace.data.get("companions") or {}))
    return {
        "version": 1,
        "workspace": workspace.name,
        "note": ("metadata about editions -- shas, offsets, page maps, licence notes. "
                 "Never a line of the text: that is what makes this file shareable."),
        "sources": {sid: _pick(body, SOURCE_KEYS) for sid, body in sorted(raw_sources.items())},
        "companions": {name: _pick(body, COMPANION_KEYS)
                       for name, body in sorted(raw_companions.items())},
    }


def receipt_for_export(receipt: dict) -> dict:
    """A run receipt with the exporter's machine taken out of it."""
    out = _pick(receipt, RECEIPT_KEYS)
    if receipt.get("source"):
        out["source"] = _pick(receipt["source"], RECEIPT_SOURCE_KEYS)
    return out


def _redact_rows(rows: Iterable[dict], texts: dict[str, str], *, redact: bool) -> list[dict]:
    """Redact every row's quote (or none of them), recording the page it is on."""
    out: list[dict] = []
    for row in rows:
        if not redact:
            out.append(dict(row))
            continue
        quote = redact_mod.quote_of(row)
        pages: list[int] = []
        if quote:
            raw = texts.get(str(row.get("source_id") or ""))
            if raw:
                pages = locate_pages(raw, quote)
        out.append(redact_mod.redact_row(row, pages=pages))
    return out


def _companions_to_export(workspace: Any, names: Sequence[str]) -> list[str]:
    known = list(workspace.companion_names())
    if not names:
        return known
    unknown = [n for n in names if n not in known]
    if unknown:
        raise CfError(
            f"this workspace has no companion named {unknown[0]!r}",
            hint=f"known: {', '.join(known) or '(none)'} -- run `cf share export` without "
                 "--companion to export every one")
    return list(names)


def _refuse_unsafe_out(out: Path, workspace: Any) -> None:
    """A bundle must not be written into a repo the no-remote rule is about.

    The export exists so that findings can travel; a bundle written inside the
    workspace repo travels nowhere (the repo has no remote, and `cf init` refuses
    one), and a bundle written inside a companion repo would be the first thing
    this skill ever wrote into a repository it is auditing -- CLAUDE.md S4,
    "never write to a companion repo".
    """
    from cfcmd.init import git_toplevel  # local: cfcmd imports cflib, not the reverse

    def inside(parent: Path) -> bool:
        parent = Path(parent).expanduser().resolve()
        return out == parent or parent in out.parents

    root = Path(workspace.root).resolve()
    if inside(root):
        raise CfError(
            f"refusing to write a share bundle inside the workspace directory ({root})",
            hint="the bundle is what LEAVES this machine and the workspace repo has no "
                 "remote; write it somewhere else, e.g. `cf share export --out "
                 "../<name>-share.tar.gz`")
    guard = git_toplevel(root)
    if guard and guard != root and inside(guard):
        raise CfError(
            f"refusing to write a share bundle inside {guard}, the git repo holding this "
            "workspace",
            hint="pick a directory outside that repo; a bundle committed there would carry "
                 "the workspace's own history rules with it")
    if inside(skill_root()):
        raise CfError(
            f"refusing to write a share bundle inside the skill repo ({skill_root()})",
            hint="the skill repo holds code, templates and docs only (CLAUDE.md S1); write "
                 "the bundle outside it")
    for name, companion in sorted(workspace.companions.items()):
        if str(companion.repo) in ("", "."):   # an unset repo resolves to the cwd
            continue
        repo = Path(str(companion.repo)).expanduser()
        if inside(repo):
            raise CfError(
                f"refusing to write a share bundle inside the companion repo {name} ({repo})",
                hint="nothing this skill runs writes to a companion repo; write the bundle "
                     "outside every audited repository")


def _stage(workspace: Any, stage: Path, *, companions: Sequence[str], redact: bool,
           include_statements: bool) -> dict:
    """Write the bundle's files into *stage*; returns the counts and the warnings."""
    paths: WorkspacePaths = workspace.paths
    texts: dict[str, str] = {}
    if redact:
        for source_id in workspace.source_ids():
            path = paths.source_text(source_id)
            if path.exists():
                texts[source_id] = path.read_text(encoding="utf-8", errors="replace")

    counts = {"companions": len(companions), "ledger_rows": 0, "routed_rows": 0,
              "tallies": 0, "receipts": 0, "statements": 0}
    quotes = {"verbatim": 0, "redacted": 0, "none": 0}
    placed = 0
    warnings: list[str] = []

    for name in companions:
        if include_statements and paths.statements_jsonl(name).exists():
            statements = paths.statements_jsonl(name)
            dest = stage / "companions" / name / statements.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(statements, dest)
            counts["statements"] += 1
        for src in (paths.accepted_jsonl(name), paths.refuted_jsonl(name)):
            if not src.exists():
                continue
            rows = read_jsonl(src)
            out_rows = _redact_rows(rows, texts, redact=redact)
            counts["ledger_rows"] += len([r for r in out_rows if "_meta" not in r])
            write_jsonl(stage / "ledger" / src.name, out_rows)
        routed = paths.routed_jsonl(name)
        if not routed.exists():
            warnings.append(f"{name}: no routed findings yet (run `cf route`)")
            continue
        raw = read_jsonl(routed)
        meta = next((r["_meta"] for r in raw if isinstance(r, dict) and "_meta" in r), None)
        rows = [r for r in raw if not (isinstance(r, dict) and "_meta" in r)]
        out_rows = _redact_rows(rows, texts, redact=redact)
        for key, value in redact_mod.count_quotes(out_rows).items():
            quotes[key] += value
        placed += sum(1 for r in out_rows
                      if redact_mod.is_redacted(r.get("book_quote"))
                      and r["book_quote"].get("page"))
        counts["routed_rows"] += len(out_rows)
        write_jsonl(stage / "ledger" / "routed" / routed.name, out_rows, meta=meta)

    # runs/ holds the prompts, the tasks and the tend root, all of which quote the
    # source; only the corpus RECEIPT -- counts, shas and the gate verdict -- travels.
    if paths.corpus_dir.is_dir():
        for run_dir in sorted(p for p in paths.corpus_dir.iterdir() if p.is_dir()):
            receipt = run_dir / "RECEIPT.json"
            if not receipt.exists():
                continue
            write_json(stage / "corpus" / run_dir.name / "RECEIPT.json",
                       receipt_for_export(read_json(receipt, default={})), sort_keys=True)
            counts["receipts"] += 1
    if paths.reports_dir.is_dir():
        for tally in sorted(paths.reports_dir.glob("tally.*.json")):
            write_json(stage / "reports" / tally.name, read_json(tally, default={}))
            counts["tallies"] += 1

    write_json(stage / SOURCES_NAME, source_metadata(workspace))
    if redact and quotes["redacted"] and not placed:
        warnings.append(
            "no redacted quote could be given a page: this workspace has no prepared text "
            "for the sources the findings cite, so `cf quotes hydrate` will have to search "
            "every page (run `cf sources refresh` to prepare the text)")
    return {"counts": counts, "quotes": quotes, "quotes_with_page": placed,
            "warnings": warnings}


def build_manifest(stage: Path, *, workspace: Any, redact: bool, companions: Sequence[str],
                   staged: dict, include_statements: bool) -> dict:
    """The manifest: every file, its sha256, and the redaction mode."""
    files = []
    for path in sorted(p for p in stage.rglob("*") if p.is_file()):
        rel = path.relative_to(stage).as_posix()
        files.append({"path": rel, "sha256": sha256_file(path),
                      "bytes": path.stat().st_size})
    return {
        "schema": BUNDLE_SCHEMA,
        "bundle_id": sha256_json({f["path"]: f["sha256"] for f in files}),
        "workspace": workspace.name,
        "companions": list(companions),
        "quotes": "redacted" if redact else "VERBATIM",
        "redaction": redact_mod.REDACTION if redact else "none",
        "statements": bool(include_statements),
        "sources": {sid: (workspace.sources[sid].sha256 or "")
                    for sid in sorted(workspace.source_ids())},
        "counts": dict(staged["counts"], quotes_with_page=staged["quotes_with_page"],
                       **{f"quotes_{k}": v for k, v in staged["quotes"].items()}),
        "withheld": list(WITHHELD),
        "files": files,
    }


def export_bundle(workspace: Any, out: str | Path, *, companions: Sequence[str] = (),
                  redact: bool = True, include_statements: bool = False,
                  force: bool = False) -> dict:
    """Write a share bundle for *workspace*.  Returns the manifest.

    *out* ending in ``.tar.gz``/``.tgz`` is one file; anything else is a
    directory.  Both carry exactly the same files, and the manifest is built from
    what was staged rather than from what was intended, so a file that failed to
    land is absent from the manifest and the verification on import says so.
    """
    out = Path(str(out)).expanduser().resolve()
    _refuse_unsafe_out(out, workspace)
    names = _companions_to_export(workspace, companions)
    if out.exists() and not force:
        if is_tarball(out) or out.is_file():
            raise CfError(f"{out} already exists",
                          hint="pass --force to overwrite it, or name another --out")
        if any(out.iterdir()):
            raise CfError(f"{out} is not empty",
                          hint="pass --force to overwrite its bundle files, or name an "
                               "empty --out directory")
    with tempfile.TemporaryDirectory(prefix="cf-share-") as tmp:
        stage = Path(tmp) / "bundle"
        stage.mkdir(parents=True)
        staged = _stage(workspace, stage, companions=names, redact=redact,
                        include_statements=include_statements)
        manifest = build_manifest(stage, workspace=workspace, redact=redact,
                                  companions=names, staged=staged,
                                  include_statements=include_statements)
        write_json(stage / MANIFEST_NAME, manifest, sort_keys=True)
        if is_tarball(out):
            out.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(out, "w:gz") as tar:
                for path in sorted(p for p in stage.rglob("*") if p.is_file()):
                    tar.add(path, arcname=path.relative_to(stage).as_posix())
        else:
            out.mkdir(parents=True, exist_ok=True)
            for path in sorted(p for p in stage.rglob("*") if p.is_file()):
                dest = out / path.relative_to(stage)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
    for warning in staged["warnings"]:
        log.warn(warning)
    manifest["out"] = str(out)
    return manifest


# --------------------------------------------------------------------------- #
# reading a bundle back
# --------------------------------------------------------------------------- #
def safe_member(name: str) -> str:
    """The relative path *name* names inside a bundle, or a refusal.

    A bundle arrives from somebody else, so its member names are input: an
    absolute path or a ``..`` component would let a tarball write over the
    receiving machine, which is the whole of CVE-2007-4559 and is why this is
    checked before anything is extracted rather than after.
    """
    raw = str(name or "")
    posix = raw.replace("\\", "/")
    if not posix or posix.strip() == "":
        raise CfError("this bundle carries a member with an empty name",
                      hint="it is not a bundle `cf share export` wrote; ask for it again")
    if posix.startswith("/") or (len(posix) > 1 and posix[1] == ":"):
        raise CfError(f"refusing a bundle member with an absolute path: {raw!r}",
                      hint="a share bundle only ever carries paths relative to itself")
    parts = [p for p in posix.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise CfError(f"refusing a bundle member that climbs out of the bundle: {raw!r}",
                      hint="a share bundle only ever carries paths relative to itself")
    return "/".join(parts)


def extract_bundle(archive: Path, dest: Path) -> Path:
    """Extract a bundle tarball into *dest*, refusing anything but plain files.

    Symlinks, hard links, devices and directories with odd permissions are all
    refused rather than filtered: a bundle is a manifest plus regular files, so
    anything else in it is a reason to stop and say so.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            rel = safe_member(member.name)
            if member.isdir():
                (dest / rel).mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise CfError(
                    f"refusing bundle member {member.name!r}: it is not a regular file",
                    hint="a share bundle carries the manifest and plain files only; "
                         "a link or a device in one means it was not written by "
                         "`cf share export`")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:  # pragma: no cover - isfile() guarantees a stream
                raise CfError(f"bundle member {member.name!r} could not be read")
            with open(target, "wb") as fh:
                shutil.copyfileobj(extracted, fh)
    return dest


def open_bundle(path: str | Path, tmp: Path) -> Path:
    """The directory holding a bundle: *path* itself, or its unpacked tarball."""
    src = Path(str(path)).expanduser().resolve()
    if src.is_dir():
        return src
    if not src.exists():
        raise CfError(f"no such bundle: {src}",
                      hint="pass the directory or the .tar.gz `cf share export --out` wrote")
    if not is_tarball(src):
        raise CfError(f"{src} is neither a bundle directory nor a {' or '.join(TAR_SUFFIXES)}",
                      hint="`cf share export --out <dir>` and `--out <name>.tar.gz` are the "
                           "two shapes `cf share import` reads")
    return extract_bundle(src, tmp)


def read_manifest(bundle: Path) -> dict:
    manifest_path = Path(bundle) / MANIFEST_NAME
    if not manifest_path.exists():
        raise CfError(f"{bundle} carries no {MANIFEST_NAME}",
                      hint="every bundle `cf share export` writes has one; without it there "
                           "is nothing to verify the files against")
    manifest = read_json(manifest_path, default={})
    if str(manifest.get("schema") or "") != BUNDLE_SCHEMA:
        raise CfError(
            f"{manifest_path} says schema {manifest.get('schema')!r}, not {BUNDLE_SCHEMA!r}",
            hint="this bundle was written by a different version of the skill; re-export it "
                 "with this one")
    return manifest


def verify_bundle(bundle: Path, manifest: dict) -> list[str]:
    """Problems with a bundle's files: missing, altered, or unlisted.

    An unlisted file is a problem, not a curiosity: the manifest is what the
    receiver checks, so a file nobody vouched for must not be quietly imported.
    """
    bundle = Path(bundle)
    problems: list[str] = []
    listed: set[str] = set()
    for entry in manifest.get("files") or []:
        rel = safe_member(entry.get("path") or "")
        listed.add(rel)
        path = bundle / rel
        if not path.exists():
            problems.append(f"missing: {rel}")
            continue
        digest = sha256_file(path)
        if digest != str(entry.get("sha256") or ""):
            problems.append(f"altered: {rel} is sha256 {short(digest)}, the manifest says "
                            f"{short(str(entry.get('sha256') or ''))}")
    for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
        rel = path.relative_to(bundle).as_posix()
        if rel == MANIFEST_NAME or rel in listed:
            continue
        problems.append(f"not in the manifest: {rel}")
    return problems


def bundle_ledger_files(bundle: Path) -> list[Path]:
    """``ledger/{accepted,refuted}.<companion>.jsonl`` inside a bundle."""
    ledger = Path(bundle) / "ledger"
    if not ledger.is_dir():
        return []
    return sorted(p for p in ledger.glob("*.jsonl")
                  if p.name.startswith(("accepted.", "refuted.")))


def bundle_routed_files(bundle: Path) -> list[Path]:
    """``ledger/routed/findings.<companion>.jsonl`` inside a bundle."""
    routed = Path(bundle) / "ledger" / "routed"
    if not routed.is_dir():
        return []
    return sorted(routed.glob("findings.*.jsonl"))


def companion_of(path: Path) -> str:
    """``accepted.adam.jsonl`` -> ``adam``; ``findings.adam.jsonl`` -> ``adam``."""
    name = os.path.basename(str(path))
    parts = name.split(".")
    return ".".join(parts[1:-1]) if len(parts) > 2 else ""
