"""Run a `cf export` convention into a companion's `pdf-overlay/annotations.json`.

New code, implementing the `cf overlay export` row of section 6 of the overlay
contract (`docs/overlay.md`).  The exporters already write a validated
annotation document; what this adds is the one step they deliberately refuse to
take, because `cf export` will not write under the checkout it reads: putting
the document where the viewer and the runtime tools look for it.

So the order is: export to a temporary file OUTSIDE the companion, check the
bytes with the vendored `formal_companion_annotations` checker (schema,
invariants and canonical form), and only then write
`<companion>/pdf-overlay/annotations.json`.  A failed export, or a document the
checker refuses, leaves whatever was there untouched -- a half-written
`annotations.json` is a blank viewer, and the companion's CI would blame its own
`check_generated.py` for it.

The write follows rule 5 like the rest of `cf overlay`: one file, under
`pdf-overlay/`, through `sync.overlay_dir`, and no git in the companion.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from cflib import log, vendored  # noqa: F401  (vendor/ on sys.path)
from cflib.errors import CfError
from cflib.paths import skill_root
from formal_companion_annotations import validate as V
from formal_companion_annotations import vocab

from overlay import sync as overlay_sync

__all__ = ["CONVENTIONS", "export", "run_cf_export"]

#: The `cf export` subcommands that write an annotation document.  `site-pages`
#: writes a page manifest, which is an INPUT to one of these, not a document;
#: `tests/test_overlay_cli.py` pins this set against the live `cf export` tree.
CONVENTIONS = ("attribution-table", "docstring-tags")

#: What a failed export prints back, at most.
TAIL = 600


def run_cf_export(argv: Sequence[str]) -> subprocess.CompletedProcess:
    """Run `cf export …` in a child process of this checkout.

    A child, not an in-process call: the exporters read an untrusted checkout
    and the command is the user-facing one, so its exit status and its output
    are what this module reports, unchanged.
    """
    command = [sys.executable, str(skill_root() / "scripts" / "cf.py"), *argv]
    return subprocess.run(command, capture_output=True, text=True, timeout=3600)


def export(companion: str | Path, *, convention: str, commit: str,
           extra: Sequence[str] = (),
           runner: Callable[[Sequence[str]], subprocess.CompletedProcess] = run_cf_export,
           ) -> Path:
    """Write `pdf-overlay/annotations.json` from `cf export <convention>`."""
    if convention not in CONVENTIONS:
        raise CfError(f"{convention!r} is not a convention that writes a document",
                      hint="--convention " + " | ".join(CONVENTIONS))
    overlay = overlay_sync.overlay_dir(companion)
    target = overlay / overlay_sync.DOCUMENT_NAME
    if target.is_symlink():
        raise CfError(f"{target} is a symlink", hint="remove it and re-run")

    workspace = Path(tempfile.mkdtemp(prefix="cf-overlay-export-"))
    try:
        written = workspace / overlay_sync.DOCUMENT_NAME
        argv = ["export", convention, str(Path(companion).expanduser()),
                "--commit", commit, "--out", str(written), *extra]
        log.detail("running cf " + " ".join(argv))
        done = runner(argv)
        if done.returncode != 0:
            output = ((done.stdout or "") + (done.stderr or "")).strip()
            raise CfError(f"cf {' '.join(argv[:2])} exited {done.returncode}",
                          hint=output[-TAIL:] or "it said nothing")
        if not written.is_file():
            raise CfError(f"cf {' '.join(argv[:2])} wrote no document",
                          hint=f"expected it at {written}")
        text = written.read_text(encoding="utf-8")
        try:
            doc = V.loads(text)
        except V.LoadError as exc:
            raise CfError(f"the exported document is not readable JSON: {exc}") from exc
        if not isinstance(doc, dict) or doc.get("schema") != vocab.ANNOTATION_SCHEMA:
            raise CfError(f"the exported document does not declare schema "
                          f"{vocab.ANNOTATION_SCHEMA}")
        problems = V.check(doc, text=text, canonical=True).problems
        if problems:
            raise CfError("the exported document does not validate, so it was not "
                          "written", hint="; ".join(problems[:5]))
        overlay.mkdir(parents=True, exist_ok=True)
        _replace(target, text)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return target


def _replace(target: Path, text: str) -> None:
    """Atomically put *text* at *target*, following no link."""
    handle, temporary = tempfile.mkstemp(dir=str(target.parent),
                                         prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:                                      # pragma: no cover
            pass
        raise
