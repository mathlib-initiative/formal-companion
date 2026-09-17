"""pdftoppm page images for the image-access ("hunt") runs.

Ported from: the RUNBOOK / HANDOFF note that the hunt run read page crops for
notation ``pdftotext`` drops (findings-hunt/exercise-1-1-14.json) and
adam-detector/README.md:43-44 (per-page splitting).

LOCAL ONLY.  Page images are copyrighted book content: they live in
``<workspace>/sources/<source_id>/images/`` (gitignored by the workspace
template), never in the skill repo, and ``cf run`` is the only thing allowed to
copy them anywhere else -- and only to an allowlisted host with recorded
consent.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Iterable, Sequence

from .compat import utcnow, write_json


def _ranges(pages: Sequence[int]) -> list[tuple[int, int]]:
    """Collapse a sorted page list into contiguous (first, last) ranges."""
    out: list[tuple[int, int]] = []
    for p in sorted({int(x) for x in pages}):
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def render(
    pdf: str,
    out_dir: str,
    pages: Iterable[int],
    *,
    dpi: int = 150,
    fmt: str = "png",
    force: bool = False,
) -> dict:
    """Render PDF pages to ``images/p-NNN.<fmt>``; returns a manifest dict.

    ``pages`` are PDF page numbers (printed + offset).  Existing images are
    kept unless ``force``, so re-running after adding items is cheap.
    """
    exe = shutil.which("pdftoppm")
    if exe is None:
        raise RuntimeError("pdftoppm not found (brew install poppler)")
    if not os.path.exists(pdf):
        raise FileNotFoundError("no such PDF: %s" % pdf)
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    wanted = sorted({int(p) for p in pages if int(p) > 0})
    todo = [p for p in wanted if force or not os.path.exists(_image_path(img_dir, p, fmt))]
    written: list[str] = []
    for first, last in _ranges(todo):
        with tempfile.TemporaryDirectory(prefix="cf-render-") as tmp:
            cmd = [
                exe, "-r", str(dpi), "-f", str(first), "-l", str(last),
                "-%s" % fmt, pdf, os.path.join(tmp, "p"),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError("pdftoppm failed (%s): %s" % (proc.returncode, proc.stderr.strip()))
            # pdftoppm pads the number to the width of the last page; rename to
            # a stable 3-digit name so prompts can name files without guessing.
            for name in sorted(os.listdir(tmp)):
                m = re.search(r"-(\d+)\.%s$" % re.escape(fmt), name)
                if not m:
                    continue
                dest = _image_path(img_dir, int(m.group(1)), fmt)
                shutil.move(os.path.join(tmp, name), dest)
                written.append(os.path.basename(dest))
    manifest = {
        "created": utcnow(),
        "dpi": dpi,
        "format": fmt,
        "requested": wanted,
        "written": sorted(written),
        "present": sorted(n for n in os.listdir(img_dir) if n.endswith("." + fmt)),
        "note": "copyrighted page images: local only, gitignored, never copied into the skill repo",
    }
    write_json(os.path.join(img_dir, "manifest.json"), manifest)
    return manifest


def _image_path(img_dir: str, page: int, fmt: str) -> str:
    return os.path.join(img_dir, "p-%03d.%s" % (page, fmt))
