"""sha256 helpers and the one canonical finding_id definition.

New code. The finding_id rule comes from the design critique ("define
finding_id as sha256(run-id, item, kind, sorted unfaithful_decls)"): the
baseline's sha256(run-id, item, concept) was unstable because `concept` is
LLM-written prose, so the same defect got a different id in every run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Sequence

_CHUNK = 1 << 20


def sha256_file(path: str | Path) -> str:
    """sha256 of a file's bytes (chunked; the pinned PDF is ~10 MB)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    """sha256 of a string's UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj) -> str:
    """sha256 of a canonical JSON encoding (sorted keys, no spaces).

    Used for adapter/config keys so the same config always hashes the same.
    """
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(blob)


def sha256_parts(parts: Iterable[object]) -> str:
    """sha256 over a sequence of strings joined by NUL (order significant)."""
    h = hashlib.sha256()
    for i, part in enumerate(parts):
        if i:
            h.update(b"\x00")
        h.update(str(part).encode("utf-8"))
    return h.hexdigest()


def short(digest: str, n: int = 12) -> str:
    """First n hex characters of a digest (for display only, never for keys)."""
    return str(digest)[:n]


def finding_id(run_id: str, source_id: str, item: str, kind: str,
               unfaithful_decls: Sequence[str]) -> str:
    """The stable id every finding carries -- the ONE definition.

    ``sha256("\\n".join([run_id, source_id, clean(item), kind, *sorted(decls)]))``.
    Deterministic for one defect WITHIN one run: ``run_id`` is the first part, so
    the same defect found by two runs gets two ids, and this is NOT the key that
    joins them.  That key is :func:`defect_key` -- the same five parts with the
    run id empty -- and ``harvest/union.py --group-by defect-key`` is what
    matches on it across runs.  An id links a ledger or routed row back to the
    finding of ONE run, which is the job it is content-addressed for, and is what
    adjudication, transcription and ``cf recheck`` join on.

    Three details are load-bearing and must not drift:

    * ``source_id`` is a part, because an item is the pair ``(source_id, item)``
      (docs/design/archive/multi-source-model.md S6.1).  Without it, ``Exercise 1.3.19``
      of one source and ``Exercise 1.3.19`` of another collide in one id -- and
      a workspace with two companions on two books would silently merge their
      findings.
    * the parts are joined with a newline, and the declarations are joined in
      as *separate parts* (not pre-joined with ``|``).  ``scripts/gate/
      verify_findings.py`` inlines the identical byte-joining because it has to
      run on a server with no ``cflib`` on the path; ``tests/test_plan_gate.py``
      asserts all five implementations agree.
    * the item label goes through :func:`cflib.items.clean` first, so
      ``Exercise    1.3.19`` and ``Exercise 1.3.19`` -- the whitespace variant
      the baseline lost -- hash to the same id.
    """
    from .items import clean  # local import: items imports nothing from here

    decls = sorted({str(d).strip() for d in (unfaithful_decls or []) if str(d).strip()})
    payload = "\n".join([str(run_id or ""), str(source_id or ""), clean(str(item or "")),
                         str(kind or ""), *decls])
    return sha256_text(payload)


def defect_key(source_id: str, item: str, kind: str,
               unfaithful_decls: Sequence[str]) -> str:
    """The run-INDEPENDENT identity of one defect -- the ONE definition.

    :func:`finding_id` with the run id empty, so two runs that found the same
    defect produce the same key.  ``finding_id`` cannot do that job (``run_id``
    is its first hashed part), which is why ``harvest/union.py --group-by
    finding-id --new-only`` once answered "everything is new" -- re-found defects
    scored as misses, the regression that module was written to fix.  Callers
    that LINK a defect across runs use this -- today ``--group-by defect-key``
    there, and nothing else; callers that address one observation of it use
    ``finding_id``, which is what adjudication, transcription and ``cf recheck``
    join on, while ``agreed`` groups on the collapsed item and the kind plus a
    shared declaration and reads neither key.  Both are carried side by side and
    neither is stored on a finding: they are recomputed from the content every
    time it is read.

    *item* must be the key two corpus generations JOIN on, not the label a
    worker wrote.  ``harvest.tally`` passes ``collapse_to_book_item()`` (which is
    ``items.join_key`` for an in-text label and the book item for a numbered
    one), because ``Exercise 1.6.27`` and ``Exercise 1.6.27(iii)`` are one defect
    at one book item: keyed on the raw spelling they landed in two groups, so the
    "exact" grouping was not even a refinement of the default one.  CLAUDE.md S4:
    anything that MATCHES on an item matches on ``join_key``.

    The companion is deliberately NOT hashed in.  It is a field on every row and
    part of every grouping tuple that uses this key (``(source_id, repo,
    defect_key)`` in ``union``), so folding it into the digest would change no
    grouping and re-key what is already written.
    """
    return finding_id("", source_id, item, kind, unfaithful_decls)


def location_key(source_id: str, item: str, library: str, commit: str,
                 declaration: str) -> str:
    """The RUN-FREE identity of one location -- the ONE definition.

    ``sha256("\\n".join([source_id, clean(item), library, commit, declaration]))``
    (docs/proposals/wikipedia-locate.md section 2.7; references/locate-documents.md).
    It is what a consumer upserts a record on, so it holds no run id: the same
    passage located at the same declaration of the same commit by two runs is one
    key, while ``finding_id`` -- which stays in the ledger -- is one per run.
    The item goes through :func:`cflib.items.clean` first, the shape
    :func:`finding_id` has, so whitespace variants of one ``In-text #<hex>`` key
    one location.  Every part is required: a key with an empty library, commit
    or declaration would collide across everything that lost the same field.
    """
    from .errors import CfError
    from .items import clean  # local import: items imports nothing from here

    parts = [str(source_id or "").strip(), clean(str(item or "")),
             str(library or "").strip(), str(commit or "").strip(),
             str(declaration or "").strip()]
    names = ("source_id", "item", "library", "commit", "declaration")
    missing = [name for name, part in zip(names, parts) if not part]
    if missing:
        raise CfError(f"a location_key needs every part; {', '.join(missing)} is empty",
                      hint="source_id, item, library, commit and declaration are all "
                           "required; the commit is the universe's, never a worker's")
    return sha256_text("\n".join(parts))


def manifest(paths: Iterable[str | Path], root: str | Path | None = None) -> dict[str, str]:
    """{relative path: sha256} for a set of files (harvest RECEIPT manifests)."""
    root_p = Path(root).resolve() if root else None
    out: dict[str, str] = {}
    for p in sorted(Path(x) for x in paths):
        key = str(p.resolve().relative_to(root_p)) if root_p else str(p)
        out[key] = sha256_file(p)
    return out
