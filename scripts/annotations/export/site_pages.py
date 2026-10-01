"""The page manifest of a rendered literate site: the file ``cf export docstring-tags --site-pages`` reads."""
from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V
from annotations.export.common import ExportError, utc

_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$")


def manifest(site: Path, *, source_id: str | None = None, title: str | None = None,
             path: str | None = None, rendered_at: str | None = None, exclude: tuple[str, ...] = ()) -> dict:
    site = site.resolve()
    if not site.is_dir():
        raise ExportError(f"{site} is not a directory", hint="name the rendered site, e.g. <checkout>/_site")
    # the producer chooses the pages it pins: generated fragments may be left out (docstring-tags counts them)
    files = sorted((p for p in site.rglob("*.html") if p.is_file()
                    and not any(fnmatch.fnmatchcase(p.relative_to(site).as_posix(), g) for g in exclude)),
                   key=lambda p: p.relative_to(site).as_posix())
    if not files:
        raise ExportError(f"{site} holds no HTML page" + (" that --exclude keeps" if exclude else ""),
                          hint="render the site first (tests/data/m3/README.md)")
    if rendered_at is not None and not _TIMESTAMP.match(rendered_at):
        raise ExportError(f"--rendered-at {rendered_at!r} is not an RFC 3339 UTC time",
                          hint="write it as 2026-09-02T00:00:00Z")
    pages = []
    for f in files:
        data = f.read_bytes()
        pages.append({"path": f.relative_to(site).as_posix(), "sha256": hashlib.sha256(data).hexdigest(),
                      "bytes": len(data)})
    out: dict = {}
    if source_id:
        out["source_id"] = source_id
    if title:
        out["title"] = title
    out["path"] = path or site.name
    out["rendered_at"] = rendered_at or utc(max(f.stat().st_mtime for f in files))
    out["site_sha256"] = V.site_sha256(pages)
    out["pages"] = pages
    return out
