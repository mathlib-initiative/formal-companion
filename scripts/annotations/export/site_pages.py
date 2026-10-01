"""The page manifest of a rendered literate site: the file ``cf export docstring-tags --site-pages`` reads.

New code.  The docstring-tags exporter pins a literate site by the sha256 of
its pages (docs/exporters.md section 2), and until now nothing in the tree
wrote that pin.  :func:`manifest` reads a rendered site and returns it in
exactly the shape :meth:`DocstringTagsExport.read_site_pin` checks:

* ``pages``: every ``*.html`` file under the site, the same walk the exporter
  makes when ``--site`` is given (a page the manifest leaves out is refused
  there), each with its POSIX path relative to the site, its sha256 and its
  byte count, ordered by path;
* ``site_sha256``: :func:`formal_companion_annotations.validate.site_sha256`
  of those pages, which the exporter recomputes and compares;
* ``path``: where the site sits under the companion's checkout (default: the
  site directory's own name, ``_site`` for the usual render);
* ``rendered_at``: an RFC 3339 UTC time, given or else the newest page's
  modification time, which the exporter carries as the site source's
  ``retrieved_at``;
* ``source_id`` and ``title`` only when given: without them the exporter
  falls back to ``<name>-site`` and a generic title.

The pages and ``site_sha256`` are what a re-render reproduces.  The other
four fields are the maintainer's words about one render, so re-pinning a
site that already has a manifest passes them on from that file
(tests/data/m3/README.md).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from formal_companion_annotations import validate as V
from annotations.export.common import ExportError, utc

#: The schema's ``timestamp``: RFC 3339 in UTC, written with Z.
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$")


def manifest(site: Path, *, source_id: str | None = None, title: str | None = None,
             path: str | None = None, rendered_at: str | None = None) -> dict:
    """The page manifest of the rendered site at ``site``."""
    site = site.resolve()
    if not site.is_dir():
        raise ExportError(f"{site} is not a directory", hint="name the rendered site, e.g. <checkout>/_site")
    files = sorted((p for p in site.rglob("*.html") if p.is_file()), key=lambda p: p.relative_to(site).as_posix())
    if not files:
        raise ExportError(f"{site} holds no HTML page", hint="render the site first (tests/data/m3/README.md)")
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
