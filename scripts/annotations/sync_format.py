"""Vendor one revision of an annotation format package into vendor/.

New code, for the switchover to formal-companion-annotations (CLAUDE.md
section 2).  Two format packages are vendored, each as one upstream revision
copied byte for byte under ``vendor/<package>/`` and sealed by
``vendor/<package>.manifest.json`` (upstream, commit, version, the tag when
there is one, and the sha256 of every vendored file):

* ``formal_companion_annotations``, the document format the exporters and
  the skill's stages read and write;
* ``literature_links``, the format the generated PDF overlay reads
  (``docs/overlay.md``): ``src/literature_links/`` plus
  ``schema/companion.schema.json``, which the package looks for in its own
  ``schema/`` directory.

``tests/test_vendored_format.py`` and ``tests/test_vendored_links.py`` fail
on any vendored byte that differs from its manifest, so a copy is never edited
here: a change to a format is a revision upstream, then this script.  Run it
by path against a local checkout of the upstream repository::

    python3 scripts/annotations/sync_format.py <checkout> v1.0.0rc2
    python3 scripts/annotations/sync_format.py <checkout> <full commit id> --package literature_links

The revision is an annotated release tag ``vMAJOR.MINOR.PATCH`` (or
``vMAJOR.MINOR.PATCHrcN``), whose version the package's ``__version__`` must
equal, or a full commit id for a release not tagged yet, in which case the
manifest names no tag.  Files are read from git through :mod:`cflib.gitsafe`,
never from the working tree, and each is checked against its blob id.
``--check`` changes nothing: it exits 1 unless the vendored tree matches the
manifest and the manifest matches the revision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib import gitsafe                                           # noqa: E402

SKILL = _SCRIPTS.parent
VENDOR = SKILL / "vendor"


@dataclass(frozen=True)
class Package:
    """One vendored package: its upstream, its directory there, and the files it carries besides."""

    name: str
    upstream: str
    source: str
    #: Upstream files copied to the same relative path inside ``vendor/<name>/``.
    extra_files: tuple[str, ...] = ()
    license: str = "Apache-2.0"


PACKAGES = {
    "formal_companion_annotations": Package(
        "formal_companion_annotations",
        "https://github.com/mathlib-initiative/formal-companion-annotations",
        "src/formal_companion_annotations"),
    "literature_links": Package(
        "literature_links",
        "https://github.com/mathlib-initiative/literature-links",
        "src/literature_links",
        extra_files=("schema/companion.schema.json",)),
}
DEFAULT_PACKAGE = "formal_companion_annotations"
#: A release tag: ``vMAJOR.MINOR.PATCH``, or a release candidate ``vMAJOR.MINOR.PATCHrcN``
#: (PEP 440 spelling, as the package's ``__version__`` writes it).
_TAG = re.compile(r"v(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:rc[1-9][0-9]*)?)")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.M)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob_id(data: bytes) -> str:
    """The git blob id of ``data``, as ``git hash-object`` computes it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def vendored_files(root: Path) -> dict[str, str]:
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


def resolve(checkout: Path, revision: str) -> tuple[str, str | None]:
    """``(commit, tag)`` of a release tag or a full commit id; the tag is None for a commit id."""
    if _TAG.fullmatch(revision):
        if git(checkout, "cat-file", "-t", f"refs/tags/{revision}").strip() != "tag":
            raise SystemExit(f"{revision}: not an annotated tag in {checkout}")
        return git(checkout, "rev-parse", "--verify", f"refs/tags/{revision}^{{commit}}").strip(), revision
    if _COMMIT.fullmatch(revision):
        if git(checkout, "cat-file", "-t", revision).strip() != "commit":
            raise SystemExit(f"{revision}: not a commit in {checkout}")
        return revision, None
    raise SystemExit(f"{revision}: not a release tag (vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCHrcN) "
                     "nor a full 40-hex commit id")


def _blobs(checkout: Path, commit: str, path: str) -> list[tuple[str, bytes]]:
    """``(upstream path, bytes)`` of every file at or under ``path``, each checked against its blob id."""
    out = []
    for line in git(checkout, "ls-tree", "-r", "--full-tree", commit, "--", path).splitlines():
        meta, name = line.split("\t", 1)
        mode, kind, oid = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SystemExit(f"{name}: not a regular file at {commit[:12]}")
        data = git(checkout, "cat-file", "blob", oid).encode("utf-8")
        if blob_id(data) != oid:
            raise SystemExit(f"{name}: the bytes read back are not the blob {oid} (not UTF-8 text?)")
        out.append((name, data))
    return out


def release(checkout: Path, revision: str, package: Package = PACKAGES[DEFAULT_PACKAGE],
            ) -> tuple[str, str | None, str, dict[str, bytes]]:
    """``(commit, tag, version, files)`` of ``package`` at ``revision``."""
    commit, tag = resolve(checkout, revision)
    files = {name[len(package.source) + 1:]: data for name, data in _blobs(checkout, commit, package.source)}
    if not files:
        raise SystemExit(f"{package.source}/ is empty at {revision}")
    for extra in package.extra_files:
        found = [data for name, data in _blobs(checkout, commit, extra) if name == extra]
        if not found:
            raise SystemExit(f"{extra} is not a file at {revision}")
        if extra in files:
            raise SystemExit(f"{extra} would replace a file of {package.source}/")
        files[extra] = found[0]
    match = _VERSION.search(files.get("__init__.py", b"").decode("utf-8"))
    if match is None:
        raise SystemExit(f"{revision}: {package.source}/__init__.py states no __version__")
    version = match.group(1)
    if tag is not None and version != _TAG.fullmatch(tag)["version"]:
        raise SystemExit(f"{tag}: the package says __version__ {version!r}, "
                         f"not {_TAG.fullmatch(tag)['version']!r}")
    return commit, tag, version, files


def manifest_for(tag: str | None, commit: str, version: str, files: dict[str, bytes],
                 package: Package = PACKAGES[DEFAULT_PACKAGE]) -> dict:
    out = {
        "upstream": package.upstream,
        "commit": commit,
        "version": version,
        "source": package.source,
        "license": package.license,
        "files": {rel: sha256(data) for rel, data in sorted(files.items())},
    }
    if tag is not None:
        out["tag"] = tag
    if package.extra_files:
        out["extra_files"] = list(package.extra_files)
    return out


def write(manifest: dict, files: dict[str, bytes], package: str, vendor: Path = VENDOR) -> None:
    target = vendor / package
    vendor.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=vendor) as tmp:
        staged = Path(tmp) / package
        for rel, data in files.items():
            (staged / rel).parent.mkdir(parents=True, exist_ok=True)
            (staged / rel).write_bytes(data)
        if vendored_files(staged) != manifest["files"]:
            raise SystemExit("the staged copy does not match its own manifest")
        if target.exists():
            shutil.rmtree(target)
        staged.rename(target)
    (vendor / f"{package}.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, vendor: Path = VENDOR) -> int:
    parser = argparse.ArgumentParser(description="Vendor one revision of an annotation format package.")
    parser.add_argument("checkout", type=Path, help="a local checkout of the package's upstream repository")
    parser.add_argument("revision", help="a release tag, vMAJOR.MINOR.PATCH (or vMAJOR.MINOR.PATCHrcN), or the "
                                         "full commit id of a release that is not tagged yet")
    parser.add_argument("--package", choices=sorted(PACKAGES), default=DEFAULT_PACKAGE,
                        help=f"the package to vendor (default {DEFAULT_PACKAGE})")
    parser.add_argument("--check", action="store_true",
                        help="change nothing; exit 1 unless vendor/ is exactly this revision")
    args = parser.parse_args(argv)
    package = PACKAGES[args.package]
    commit, tag, version, files = release(args.checkout.resolve(), args.revision, package)
    manifest = manifest_for(tag, commit, version, files, package)
    label = f"{tag} ({commit[:12]})" if tag else commit[:12]
    if args.check:
        path = vendor / f"{package.name}.manifest.json"
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        ok = current == manifest and vendored_files(vendor / package.name) == manifest["files"]
        print(f"vendor/{package.name}: {'is' if ok else 'is NOT'} {label}")
        return 0 if ok else 1
    write(manifest, files, package.name, vendor)
    print(f"vendor/{package.name}: {label}, version {version}, {len(files)} files; "
          f"now run the suite and commit vendor/ with the manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
