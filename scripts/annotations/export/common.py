"""What both exporters share: the pinned checkout, the report, the source identity.

New code, ported from the two prototype exporters of docs/annotations.md
section 10 (the parts they had in common) and implementing
docs/proposals/pdf-to-companion.md section 5.4.

* :func:`pinned_checkout` reads the checkout through ``cflib.gitsafe`` (a
  companion tree is data, never a program to run) and refuses a tree that is
  not at the pinned commit or is dirty: the document names ``companion.commit``
  as the bytes it describes, so anything else would be a lie.
* :func:`companion_identity` derives ``companion.name`` and ``companion.repo``
  from the checkout's ``origin`` remote, so no exporter carries a repository
  name in its code.
* :func:`registry_source` names a PDF the way the skill names it: by the
  sha256 of its bytes, through ``registry/sources.yaml``.  The two prototypes
  gave one edition two ids; one id per edition is the registry's rule.
* :class:`Report` collects the ``lossy`` and ``bent`` buckets (what the source
  data holds that the format cannot carry, and what was carried in a shape the
  format did not intend), the counts and any problems.  It is written beside
  the document.
* :class:`HeaderCutter` cuts a named declaration's section 5.1 signature with
  ``annotations.ids``, from the file and the keyword's line.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from annotations import ids                                          # noqa: E402
from annotations import validate as V                                # noqa: E402
from cflib import __version__, gitsafe                               # noqa: E402
from cflib.jsonio import atomic_write                                # noqa: E402

#: Keywords whose header opens fields or constructors, not a value: no signature.
NO_HEADER_CUT = frozenset({"structure", "class", "inductive"})


class ExportError(Exception):
    """A refusal: the inputs do not describe one pinned companion.  ``hint`` says what settles it."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class Report:
    """The export's own account: counts, the lossy and bent buckets, problems.

    A *lossy* bucket is source data the document cannot carry; a *bent* one is
    data carried in a shape the format did not intend (an ``x_`` key, a value
    parsed from prose).  Each bucket has a count, one sentence of detail and
    up to five examples.
    """

    def __init__(self) -> None:
        self.counts: dict[str, Any] = {}
        self.lossy: dict[str, dict] = {}
        self.bent: dict[str, dict] = {}
        self.problems: list[str] = []

    def _note(self, bucket: dict, key: str, count: int, detail: str, examples=()) -> None:
        entry = bucket.setdefault(key, {"count": 0, "detail": detail, "examples": []})
        entry["count"] += count
        for example in examples:
            if len(entry["examples"]) < 5:
                entry["examples"].append(example)

    def lose(self, key: str, count: int, detail: str, examples=()) -> None:
        self._note(self.lossy, key, count, detail, examples)

    def bend(self, key: str, count: int, detail: str, examples=()) -> None:
        self._note(self.bent, key, count, detail, examples)

    def problem(self, message: str) -> None:
        self.problems.append(message)

    def to_dict(self) -> dict:
        return {"counts": self.counts, "lossy": self.lossy, "bent": self.bent,
                "problems": self.problems}


# ---------------------------------------------------------------------------
# files and json
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_lock(root: Path, rel: str) -> dict:
    """``{path, sha256, bytes}`` of one file of the checkout, as a provenance input."""
    data = (root / rel).read_bytes()
    return {"path": rel, "sha256": sha256_bytes(data), "bytes": len(data)}


def load_json(path: Path) -> Any:
    """A JSON file, gzip-compressed when its name ends in ``.gz``."""
    if str(path).endswith(".gz"):
        with gzip.open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def read_bytes(path: Path) -> bytes:
    """The bytes of a file, decompressed when its name ends in ``.gz``."""
    if str(path).endswith(".gz"):
        with gzip.open(path, "rb") as fh:
            return fh.read()
    return path.read_bytes()


def utc(epoch: int | float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def module_of(rel: str) -> str:
    """``TaoMeasure/Section_1_1_1.lean`` -> ``TaoMeasure.Section_1_1_1``."""
    return rel[:-len(".lean")].replace("/", ".") if rel.endswith(".lean") else rel


def generator(convention: str) -> str:
    """The ``companion.generator`` string: the command and the skill's version."""
    return f"cf export {convention} (companion-faithfulness {__version__})"


def write_canonical(path: Path, doc: dict) -> str:
    """Write ``doc`` in canonical form (docs/annotations.md section 7); return the text."""
    text = V.canonical_dumps(V.canonicalize(doc))
    atomic_write(path, text)
    return text


def write_report(path: Path, report: Report) -> None:
    atomic_write(path, json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# the checkout
# ---------------------------------------------------------------------------

def git_out(repo: Path, *args: str) -> str:
    """One read-only git command in the checkout, through the gitsafe boundary."""
    try:
        proc = gitsafe.run(repo, list(args))
    except OSError as exc:
        raise ExportError(f"git is not available: {exc}") from exc
    if proc.returncode != 0:
        raise ExportError(f"git {' '.join(args)} failed in {repo}: {(proc.stderr or '').strip()}",
                          hint="the exporter needs a git checkout of the companion")
    return proc.stdout.strip()


def pinned_checkout(repo: Path, commit: str) -> tuple[str, str]:
    """``(HEAD, its date)``, refusing a checkout that is not at ``commit`` or is dirty."""
    head = git_out(repo, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{7,64}", commit or ""):
        raise ExportError(f"{commit!r} is not a commit id", hint="pass --commit <full or abbreviated sha>")
    if not head.startswith(commit):
        raise ExportError(f"{repo} is at {head[:12]}, not at the pinned {commit}",
                          hint=f"check out {commit} in a scratch clone; the exporter never touches the tree")
    dirty = git_out(repo, "status", "--porcelain")
    if dirty:
        raise ExportError(f"{repo} has uncommitted changes; the document would name {head[:12]} "
                          f"for bytes that differ from it",
                          hint="export from a clean checkout of the pinned commit")
    epoch = int(git_out(repo, "log", "-1", "--format=%ct"))
    return head, utc(epoch)


_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def repo_id_from_url(url: str) -> str | None:
    """``host/org/name`` from a remote URL (``https://``, ``ssh://`` or ``git@host:``), or None."""
    url = url.strip()
    if not url:
        return None
    if _SCHEME.match(url):
        rest = _SCHEME.sub("", url)
        rest = rest.split("@", 1)[-1]                        # user@host
    elif ":" in url and "/" not in url.split(":", 1)[0]:
        host, _, path = url.partition(":")
        rest = host.split("@", 1)[-1] + "/" + path.lstrip("/")
    else:
        return None
    rest = rest.strip("/")
    if rest.endswith(".git"):
        rest = rest[:-4]
    parts = [p for p in rest.split("/") if p]
    if len(parts) < 2 or any(ch in rest for ch in " #?"):
        return None
    host = parts[0].split(":", 1)[0]                          # drop a port
    return "/".join([host, *parts[1:]])


def companion_identity(repo: Path, name: str | None = None,
                       repo_id: str | None = None) -> tuple[str, str]:
    """``(companion.name, companion.repo)``: the flags, else the ``origin`` remote."""
    if repo_id is None:
        proc = gitsafe.run(repo, ["remote", "get-url", "origin"])
        repo_id = repo_id_from_url(proc.stdout) if proc.returncode == 0 else None
    if not repo_id:
        raise ExportError(f"{repo} has no origin remote to name the companion by",
                          hint="pass --repo-id host/org/name (and --name)")
    return (name or repo_id.rsplit("/", 1)[-1]), repo_id


# ---------------------------------------------------------------------------
# the source
# ---------------------------------------------------------------------------

def registry_source(sha256: str):
    """The registry entry for the bytes with this sha256, or None when the skill does not know them."""
    from cflib.registry import Registry
    return Registry.load(None).by_sha(sha256)


def pdf_source_fields(sha256: str, *, source_id: str | None = None, title: str | None = None,
                      edition: str | None = None, offset: int | None = None) -> dict:
    """``id``, ``title``, ``edition`` and ``printed_page_offset`` of a PDF source.

    The registry answers when it knows the bytes; a flag overrides a field it
    knows and supplies one it does not.  A PDF the registry has never met needs
    all of ``source_id``, ``title`` and ``offset``.
    """
    entry = registry_source(sha256)
    fields: dict = {}
    if entry is not None:
        fields = {"id": entry.id, "title": entry.title}
        if entry.edition:
            fields["edition"] = entry.edition
        if entry.offset is not None:
            fields["printed_page_offset"] = int(entry.offset)
    if source_id:
        fields["id"] = source_id
    if title:
        fields["title"] = title
    if edition:
        fields["edition"] = edition
    if offset is not None:
        fields["printed_page_offset"] = int(offset)
    missing = [k for k in ("id", "title", "printed_page_offset") if k not in fields]
    if missing:
        raise ExportError(f"the registry does not know the PDF {sha256[:12]}...; missing {missing}",
                          hint="pass --source-id, --source-title and --page-offset (and --source-edition)")
    return fields


# ---------------------------------------------------------------------------
# the section 5.1 header cut for named declarations
# ---------------------------------------------------------------------------

class HeaderCutter:
    """``(signature, statement_sha256)`` of a named declaration at (file, keyword line).

    The section 5.1 cut (the keyword up to the ``:=`` or ``where`` that ends a
    typed header, comments removed, whitespace collapsed), by
    ``annotations.ids``' own scanner.  ``skipped`` counts why a declaration got
    none: a ``structure``, ``class`` or ``inductive`` (their ``where`` opens
    fields), a header nothing ends, or a line the keyword is not on.
    """

    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[str, tuple[str, str]] = {}
        self.skipped: dict[str, int] = {}

    def code(self, rel: str) -> tuple[str, str]:
        if rel not in self.cache:
            code = ids.strip_comments((self.root / rel).read_text(encoding="utf-8"))[0]
            self.cache[rel] = (code, ids.blank_literals(code))
        return self.cache[rel]

    def _skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def cut(self, rel: str, line: int, kind: str) -> tuple[str, str] | None:
        if kind in NO_HEADER_CUT:
            self._skip(f"kind_{kind}")
            return None
        code, scan = self.code(rel)
        pos = 0
        for _ in range(line - 1):
            pos = scan.index("\n", pos) + 1
        eol = scan.find("\n", pos)
        eol = len(scan) if eol < 0 else eol
        start = next((i for i in range(pos, eol) if ids.token_at(scan, i, kind)), None)
        if start is None:
            self._skip("keyword_not_on_line")
            return None
        sig = ids.named_signature(code, scan, start, kind)
        if sig is None:
            self._skip("no_header_end")
            return None
        return sig, ids.statement_sha256(sig)
