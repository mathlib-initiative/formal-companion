"""Vendor one tagged release of formal-companion-annotations into vendor/.

New code, for the switchover to formal-companion-annotations (CLAUDE.md
section 2).  The annotation format is its own repository; the skill carries
one release's package under ``vendor/formal_companion_annotations/`` and a
sealed manifest beside it, ``vendor/formal_companion_annotations.manifest.json``:
the upstream URL, the tag, the commit it names, the version, and the sha256 of
every vendored file.  ``tests/test_vendored_format.py`` fails on any byte that
differs from the manifest, so the copy is never edited here: a change to the
format is a release upstream, then this script, then the skill's own changes.

Run by the maintainer, by path, against a local checkout of the upstream
repository that has the tag::

    python3 scripts/annotations/sync_format.py <formal-companion-annotations checkout> v1.0.0

A release candidate is tagged ``vMAJOR.MINOR.PATCHrcN`` (``v1.0.0rc1``) and is
vendored the same way; the manifest then names that tag and the package's
``__version__`` must be ``1.0.0rc1``.

It copies the package as the tag has it (``git ls-tree`` and ``git cat-file``
through :mod:`cflib.gitsafe`, never the checkout's working tree), checks each
file against the tag's blob id, checks that the package's ``__version__`` is
the tag's, and then replaces the vendored tree and the manifest.  ``--check``
changes nothing: it exits 1 unless the vendored tree matches the manifest and
the manifest matches the tag in the checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib import gitsafe                                           # noqa: E402

SKILL = _SCRIPTS.parent
VENDOR = SKILL / "vendor"
PACKAGE = "formal_companion_annotations"
TARGET = VENDOR / PACKAGE
MANIFEST = VENDOR / f"{PACKAGE}.manifest.json"
UPSTREAM = "https://github.com/mathlib-initiative/formal-companion-annotations"
#: Where the package sits in the upstream tree.
SOURCE = f"src/{PACKAGE}"
#: A release tag: ``vMAJOR.MINOR.PATCH``, or a release candidate ``vMAJOR.MINOR.PATCHrcN``
#: (PEP 440 spelling, as the package's ``__version__`` writes it).
_TAG = re.compile(r"v(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:rc[1-9][0-9]*)?)")
_VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.M)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob_id(data: bytes) -> str:
    """The git blob id of ``data``, as ``git hash-object`` computes it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def vendored_files(root: Path = TARGET) -> dict[str, str]:
    """``{path relative to the package: sha256}`` of every vendored file, bytecode left out."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and "__pycache__" not in rel.parts and path.suffix != ".pyc":
            out[rel.as_posix()] = sha256(path.read_bytes())
    return out


def git(checkout: Path, *args: str) -> str:
    proc = gitsafe.run(checkout, list(args))
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {checkout}: {proc.stderr.strip()}")
    return proc.stdout


def release(checkout: Path, tag: str) -> tuple[str, dict[str, bytes]]:
    """The commit ``tag`` names and the package's files at it, each checked against its blob id."""
    if _TAG.fullmatch(tag) is None:
        raise SystemExit(f"{tag}: not a release tag (vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCHrcN)")
    if git(checkout, "cat-file", "-t", f"refs/tags/{tag}").strip() != "tag":
        raise SystemExit(f"{tag}: not an annotated tag in {checkout}")
    commit = git(checkout, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").strip()
    files: dict[str, bytes] = {}
    for line in git(checkout, "ls-tree", "-r", "--full-tree", commit, "--", SOURCE).splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, oid = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SystemExit(f"{path}: not a regular file at {tag}")
        data = git(checkout, "cat-file", "blob", oid).encode("utf-8")
        if blob_id(data) != oid:
            raise SystemExit(f"{path}: the bytes read back are not the blob {oid} (not UTF-8 text?)")
        files[path[len(SOURCE) + 1:]] = data
    if not files:
        raise SystemExit(f"{SOURCE}/ is empty at {tag}")
    found = _VERSION.search(files["__init__.py"].decode("utf-8"))
    version = _TAG.fullmatch(tag)["version"]
    if found is None or found.group(1) != version:
        raise SystemExit(f"{tag}: the package says __version__ {found and found.group(1)!r}, not {version!r}")
    return commit, files


def manifest_for(tag: str, commit: str, files: dict[str, bytes]) -> dict:
    return {
        "upstream": UPSTREAM,
        "tag": tag,
        "commit": commit,
        "version": _TAG.fullmatch(tag)["version"],
        "source": SOURCE,
        "license": "Apache-2.0",
        "files": {rel: sha256(data) for rel, data in sorted(files.items())},
    }


def write(manifest: dict, files: dict[str, bytes]) -> None:
    VENDOR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=VENDOR) as tmp:
        staged = Path(tmp) / PACKAGE
        for rel, data in files.items():
            (staged / rel).parent.mkdir(parents=True, exist_ok=True)
            (staged / rel).write_bytes(data)
        if vendored_files(staged) != manifest["files"]:
            raise SystemExit("the staged copy does not match its own manifest")
        if TARGET.exists():
            shutil.rmtree(TARGET)
        staged.rename(TARGET)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vendor a formal-companion-annotations release.")
    parser.add_argument("checkout", type=Path, help="a local checkout of formal-companion-annotations")
    parser.add_argument("tag", help="the release tag, vMAJOR.MINOR.PATCH (or vMAJOR.MINOR.PATCHrcN for a "
                                    "release candidate)")
    parser.add_argument("--check", action="store_true",
                        help="change nothing; exit 1 unless vendor/ is exactly this release")
    args = parser.parse_args(argv)
    commit, files = release(args.checkout.resolve(), args.tag)
    manifest = manifest_for(args.tag, commit, files)
    if args.check:
        current = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else None
        ok = current == manifest and vendored_files() == manifest["files"]
        print(f"vendor/{PACKAGE}: {'is' if ok else 'is NOT'} {args.tag} ({commit[:12]})")
        return 0 if ok else 1
    write(manifest, files)
    print(f"vendor/{PACKAGE}: {args.tag} ({commit[:12]}), {len(files)} files; "
          f"now run the suite and commit vendor/ with the manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
