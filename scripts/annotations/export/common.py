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
* :func:`repo_relative` checks a path an input flag names: an exporter reads a
  checkout at a pinned commit, so every input it is pointed at is a path
  *inside* that checkout.
* :func:`registry_source` names a PDF the way the skill names it: by the
  sha256 of its bytes, through ``registry/sources.yaml``.  The two prototypes
  gave one edition two ids; one id per edition is the registry's rule.
* :class:`Report` collects the ``lossy`` and ``bent`` buckets (what the source
  data holds that the format cannot carry, and what was carried in a shape the
  format did not intend), the counts and any problems.  It is written beside
  the document.
* :class:`HeaderCutter` cuts a named declaration's section 7.1 signature with
  ``formal_companion_annotations.ids.signature_at``, from the file and the
  keyword's line.
* :func:`render_sub` is the one sub-part renderer both exporters use, so a
  label and a tag naming the same part give the same ``sub`` (section 5.2 of
  docs/annotations.md keys numbered items by ``(source, kind, number, sub)``).
* :func:`declare_profiles` writes the root ``profiles`` object from what the
  document holds: each of the format's four profiles exactly when one of the
  keys it owns is present (invariant I14), at the version the vendored package
  knows.  A source's ``role`` is core (1.0.0rc1 and later), so a document whose only
  profile-shaped key is ``role`` declares nothing.
* :func:`report_warnings` is what a successful export's counts say that the
  user must see anyway: an input nothing read, a snippet not located, and a
  snippet cut at a line the document does not give for its declaration.
* :func:`has_url` is the exporters' own guard for the format's ``external-urls``
  lint (``L1``): a note, a title or a raw label that holds a URL is dropped and
  reported, so the documents pass the lint the viewer's deployment relies on.
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

from cflib import vendored  # noqa: E402,F401  (vendor/ on sys.path)
from formal_companion_annotations import ids                         # noqa: E402
from formal_companion_annotations import validate as V               # noqa: E402
from formal_companion_annotations import vocab                       # noqa: E402
from cflib import __version__, gitsafe                               # noqa: E402
from cflib import items as cf_items                                  # noqa: E402
from cflib.jsonio import atomic_write                                # noqa: E402

#: Keywords whose header opens fields or constructors, not a value: no signature
#: (``ids.signature_at`` returns None for them; counted apart in the cutter's ``skipped``).
NO_HEADER_CUT = frozenset({"structure", "class", "inductive"})
#: The lint the exporters' documents are checked with (``validate.LINTS``).
LINTS = ("external-urls",)
#: A sub-part as the format writes it (the schema's ``sub`` pattern): ``ii``, ``i.a``, ``iv-vi``, ``i,iii``,
#: and a primed part ``b'`` or ``f''`` (the prime stays, in the item id too: ``exercise-1.4-b'``).
SUB_PATTERN = re.compile(r"^[0-9A-Za-z]+'*(?:[.,-][0-9A-Za-z]+'*)*$")
#: What joins two sub-part groups: a dash is a range, a comma, slash, ampersand or "and" a list.
_SUB_JOIN = re.compile(r"^\s*(?:(?P<range>[-‐–—])|(?P<list>,|/|&|\band\b))\s*(?=\()")
_PAREN = re.compile(r"^\(([^()]*)\)")
_URL = re.compile(r"https?://", re.I)


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


def has_url(text: str | None) -> bool:
    """Whether ``text`` holds an ``http(s)://`` URL.

    The format's ``external-urls`` lint (``L1``, docs/annotations.md section
    9.5) allows a URL only in a source's ``url`` and ``alternate_urls``, a
    destination's ``href``, a selector's ``conformsTo`` and quoted source
    text.  The exporters keep their documents lint-clean: a value that would
    fail it is dropped and reported as a problem.
    """
    return bool(text) and bool(_URL.search(text))


def declare_profiles(doc: dict) -> dict:
    """Set the root ``profiles`` of ``doc`` to exactly the profiles its keys need (I14).

    A profile is declared when one of the root arrays or record fields it
    owns (``validate.PROFILES``) is present, empty or not, at the version the
    vendored package knows; an absent profile key is removed.  Returns the
    ``profiles`` object written (empty when the document is core only).  What
    each profile owns is the package's word, not this module's: ``audit`` owns
    ``verdicts`` alone (a source's ``role`` is core), so a document with no
    verdict declares no ``audit``.
    """
    needed: dict[str, str] = {}
    for name, owned in V.PROFILES.items():
        present = any(array in doc for array in owned["arrays"])
        for dotted in owned["fields"]:
            head, _, field_name = dotted.partition(".")
            if head == "companion":
                present = present or field_name in (doc.get("companion") or {})
            else:
                present = present or any(field_name in rec for rec in doc.get(head) or [])
        if present:
            needed[name] = vocab.ANNOTATION_PROFILE_VERSIONS[name]
    if needed:
        doc["profiles"] = dict(sorted(needed.items()))
    else:
        doc.pop("profiles", None)
    return dict(needed)


# ---------------------------------------------------------------------------
# sub-parts
# ---------------------------------------------------------------------------

def after_number(raw: str, number: str) -> str:
    """The text of ``raw`` that follows the item number ``number`` (empty when it is not there)."""
    i = raw.find(number)
    return raw[i + len(number):] if i >= 0 else ""


def render_sub(text: str) -> tuple[str, str, str | None]:
    """``(sub, rest, refused)``: the sub-part written after an item's number, in the format's spelling.

    One renderer for both exporters, so the two agree on the ``(source, kind,
    number, sub)`` key of docs/annotations.md section 3.2.  The groups are the
    item grammar's (``cflib.items.parse_subs``): consecutive groups nest
    (``(i)(a)`` -> ``i.a``), a dash between groups is a range (``(i)-(iii)`` ->
    ``i-iii``) and a comma, slash, ampersand or "and" a list (``(i), (iii)`` ->
    ``i,iii``), each emitted whole rather than narrowed to its first group.
    A primed part (``(b')``, ``(f'')``) is a group like any other: the prime
    stays (``b'``), as the format's ``sub`` admits.

    ``refused`` is the raw sub-part text when it cannot be carried: a
    parenthetical attached to the number that the grammar does not accept as
    a sub-part (``1.2.3(ii*)``), a group after a range or list separator that
    it does not accept, or a result outside :data:`SUB_PATTERN`.  The sub is
    then empty, ``rest`` is ``text`` unchanged, and the caller reports it.  A
    parenthetical set off by a space and refused by the grammar
    (``1.2.5 (Finite additivity)``) is a title, not a sub-part: ``("", text,
    None)``.
    """
    attached = _PAREN.match(text)
    text = text.strip()
    groups, rest = cf_items.parse_subs(text)
    if not groups:
        if attached:
            return "", text, attached.group(0)
        return "", text, None
    parts = [".".join(re.findall(r"\(([^()]*)\)", groups))]
    while True:
        join = _SUB_JOIN.match(rest)
        if not join:
            break
        more, after = cf_items.parse_subs(rest[join.end():])
        if not more:
            # the separator promises a sub-part the grammar refuses: carrying the first group alone narrows
            nxt = _PAREN.match(rest[join.end():])
            consumed = len(text) - len(rest) + join.end() + (len(nxt.group(0)) if nxt else 0)
            return "", text, text[:consumed].strip()
        parts.append("-" if join.group("range") else ",")
        parts.append(".".join(re.findall(r"\(([^()]*)\)", more)))
        rest = after
    sub = "".join(parts)
    if not SUB_PATTERN.match(sub):
        return "", text, text[:len(text) - len(rest)].strip()
    return sub, rest, None


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


def report_warnings(counts: dict) -> list[str]:
    """What a report's counts say that the user must see though the export succeeded."""
    out = [f"{rel} is not read; it can be deleted" for rel in counts.get("ignored_inputs") or []]
    unlocated = counts.get("snippets_unlocated") or []
    if unlocated:
        out.append(f"no snippet for {len(unlocated)} of the ids it links (not in the Lean file the "
                   f"document names for them): {', '.join(unlocated[:5])}"
                   + (", …" if len(unlocated) > 5 else ""))
    stripped = counts.get("titles_not_printed") or []
    if stripped:
        out.append(f"{len(stripped)} title(s) could not be read from the PDF, so they are the "
                   f"words with their TeX markup stripped and not what the page prints: "
                   + ", ".join(stripped[:5]) + (", …" if len(stripped) > 5 else ""))
    unread = counts.get("titles_glyphs_unread") or []
    if unread:
        out.append(f"{len(unread)} title(s) were read without the page's own glyphs "
                   f"(poppler's pdftohtml is not on PATH, or what it read is not what "
                   f"pdftotext cropped), so they are the flattened text, which prints a "
                   f"double-struck letter and a script plainly: "
                   + ", ".join(unread[:5]) + (", …" if len(unread) > 5 else ""))
    differs = counts.get("snippets_line_differs") or []
    if differs:
        out.append(f"{len(differs)} snippet(s) were cut at a line the document does not give for "
                   f"the declaration, so the source read may not be the pinned one: "
                   + ", ".join(differs[:5]) + (", …" if len(differs) > 5 else ""))
    return out


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


def repo_relative(rel: str | Path, flag: str) -> str:
    """One input path inside the checkout, as a ``/``-joined relative path.

    An exporter reads a companion at a pinned commit, so a flag that moves one
    of its inputs moves it *within* that checkout: an absolute path, or one that
    climbs out with ``..``, is refused rather than read.
    """
    text = str(rel).replace("\\", "/").strip().rstrip("/")
    parts = [p for p in text.split("/") if p and p != "."]
    if not parts or text.startswith("/") or ".." in parts or Path(text).is_absolute():
        raise ExportError(f"{flag} {str(rel)!r} is not a path inside the checkout",
                          hint="the exporter reads the companion at the pinned commit and writes nothing "
                               "under it; give a path relative to the checkout root")
    return "/".join(parts)


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
# the section 7.1 header cut for named declarations
# ---------------------------------------------------------------------------

class HeaderCutter:
    """``(signature, statement_sha256)`` of a named declaration at (file, keyword line).

    The section 7.1 cut (the keyword up to the ``:=``, top-level ``where`` or
    match alternative that ends a typed header; the display form keeps the
    readable text, the hash is over the token-normalised form), by
    ``formal_companion_annotations.ids.signature_at``.  ``skipped`` counts why
    a declaration got none: a ``structure``, ``class`` or ``inductive`` (their
    ``where`` opens fields), or a header the scanner does not cut (untyped,
    nothing ends it, or no declaration of that keyword starts on the line).
    """

    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[str, str] = {}
        self.skipped: dict[str, int] = {}

    def text(self, rel: str) -> str:
        if rel not in self.cache:
            self.cache[rel] = (self.root / rel).read_text(encoding="utf-8")
        return self.cache[rel]

    def _skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def cut(self, rel: str, line: int, kind: str) -> tuple[str, str] | None:
        if kind in NO_HEADER_CUT:
            self._skip(f"kind_{kind}")
            return None
        got = ids.signature_at(self.text(rel), line, kind)
        if got is None:
            self._skip("no_typed_header")
            return None
        return got
