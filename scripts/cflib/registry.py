"""The source registry: what the skill remembers about a known edition.

docs/design/archive/multi-source-model.md R3 / S2.  Two files, same schema, different
authority:

    <skill>/registry/sources.yaml   curated, shipped, committed here; no command
                                    writes it
    <workspace>/registry.yaml       the workspace's overlay; `cf onboard` and
                                    `cf sources add` append to it, always with
                                    ``confirmed: false``

Lookup order is OVERLAY FIRST, then the skill, merged field by field (the
overlay's non-null keys win).  A human promotes an entry by editing the skill's
registry and deleting the overlay row.

**The registry never stores source text.**  Metadata, URLs, shas, page maps and
licence notes only.  That is a copyright boundary, not a convention: it is what
makes it safe to commit the registry to a public repo while the text it
describes stays gitignored in a workspace.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Container, Iterable, Sequence

from .errors import CfError
from .jsonio import atomic_write
from .paths import registry_dir
from .yamlio import dump_yaml, read_yaml

REGISTRY_VERSION = 1
REGISTRY_FILE = "sources.yaml"
OVERLAY_FILE = "registry.yaml"

OVERLAY_HEADER = (
    "# registry.yaml -- this WORKSPACE's overlay on the skill's source registry.\n"
    "# Written by `cf onboard` / `cf sources add`, always with confirmed: false.\n"
    "# Metadata only -- no source text, ever. Promote an entry by copying it into\n"
    "# the skill's registry/sources.yaml (confirmed: true) and deleting it here.\n"
)


def skill_registry_path() -> Path:
    """``<skill>/registry/sources.yaml`` -- the curated, shipped registry."""
    return registry_dir() / REGISTRY_FILE


def _root_of(workspace: Any) -> Path:
    """The directory holding the overlay, from a Workspace OR a bare path.

    NOT ``getattr(workspace, "root", workspace)``: a ``pathlib.Path`` HAS a
    ``.root`` attribute and it is ``"/"``, so that spelling silently redirected
    every overlay read and write to the filesystem root.
    """
    return Path(workspace if isinstance(workspace, (str, Path)) else workspace.root)


@dataclass(frozen=True)
class RegistryEntry:
    """One known edition. Metadata only -- never a line of the book itself."""

    id: str
    title: str = ""
    authors: tuple[str, ...] = ()
    edition: str = ""
    isbn: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    pages: int | None = None
    download_url: str | None = None
    book_page_url: str | None = None
    errata_url: str | None = None
    licence: dict = field(default_factory=dict)
    offset: int | None = None
    offset_confidence: float | None = None
    offset_method: str = "registry"
    kinds: tuple[str, ...] = ()
    same_work: tuple[str, ...] = ()
    page_map_to: dict = field(default_factory=dict)
    aliases: dict = field(default_factory=dict)
    confirmed: bool = False
    notes: str = ""
    origin: str = "skill"

    @classmethod
    def from_dict(cls, source_id: str, data: dict, origin: str = "skill") -> "RegistryEntry":
        data = dict(data or {})
        aliases = dict(data.get("aliases") or {})
        return cls(
            id=source_id,
            title=str(data.get("title") or ""),
            authors=_strs(data.get("authors")),
            edition=str(data.get("edition") or ""),
            isbn=(str(data["isbn"]) if data.get("isbn") else None),
            sha256=(str(data["sha256"]).strip().lower() if data.get("sha256") else None),
            bytes=(int(data["bytes"]) if data.get("bytes") is not None else None),
            pages=(int(data["pages"]) if data.get("pages") is not None else None),
            download_url=(str(data["download_url"]) if data.get("download_url") else None),
            book_page_url=(str(data["book_page_url"]) if data.get("book_page_url") else None),
            errata_url=(str(data["errata_url"]) if data.get("errata_url") else None),
            licence=dict(data.get("licence") or {}),
            offset=(int(data["offset"]) if data.get("offset") is not None else None),
            offset_confidence=(float(data["offset_confidence"])
                               if data.get("offset_confidence") is not None else None),
            offset_method=str(data.get("offset_method") or "registry"),
            kinds=_strs(data.get("kinds")),
            same_work=_strs(data.get("same_work")),
            page_map_to=dict(data.get("page_map_to") or {}),
            aliases={
                "sha256_prefixes": [str(x).lower() for x in (aliases.get("sha256_prefixes") or [])],
                "urls": [str(x) for x in (aliases.get("urls") or [])],
                "filenames": [str(x) for x in (aliases.get("filenames") or [])],
                "titles": [str(x) for x in (aliases.get("titles") or [])],
            },
            confirmed=bool(data.get("confirmed")),
            notes=str(data.get("notes") or ""),
            origin=origin,
        )

    def to_dict(self) -> dict:
        """The registry-file body of this entry (``origin`` is not stored)."""
        return {
            "title": self.title,
            "authors": list(self.authors),
            "edition": self.edition,
            "isbn": self.isbn,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "pages": self.pages,
            "download_url": self.download_url,
            "book_page_url": self.book_page_url,
            "errata_url": self.errata_url,
            "licence": dict(self.licence),
            "offset": self.offset,
            "offset_confidence": self.offset_confidence,
            "offset_method": self.offset_method,
            "kinds": list(self.kinds),
            "same_work": list(self.same_work),
            "page_map_to": dict(self.page_map_to),
            "aliases": dict(self.aliases),
            "confirmed": self.confirmed,
            "notes": self.notes,
        }

    def to_source_fields(self) -> dict:
        """The ``workspace.yaml sources.<id>`` body this entry implies."""
        return {
            "sha256": self.sha256,
            "title": self.title,
            "authors": list(self.authors),
            "edition": self.edition,
            "isbn": self.isbn,
            "kinds": list(self.kinds),
            "offset": self.offset,
            "offset_confidence": self.offset_confidence,
            "offset_method": self.offset_method,
            "pages": self.pages,
            "bytes": self.bytes,
            "errata_url": self.errata_url,
            "errata_confidence": (1.0 if self.errata_url else 0.0),
            "download_url": self.download_url,
            "book_page_url": self.book_page_url,
            "licence": dict(self.licence),
            "same_work": list(self.same_work),
            "page_map_to": dict(self.page_map_to),
            "confirmed": self.confirmed,
            "registry": self.origin,
        }

    # -- licence -------------------------------------------------------
    def may_fetch(self) -> bool:
        """True when the author publishes this file and a fetch is permitted."""
        return bool(self.download_url) and str(self.licence.get("fetch") or "none") != "none"


class Registry:
    """The skill registry with a workspace overlay on top."""

    def __init__(self, entries: dict[str, RegistryEntry], *,
                 skill_path: Path | None = None, overlay_path: Path | None = None,
                 shadowed: Sequence[str] = ()):
        self.entries = entries
        self.skill_path = skill_path
        self.overlay_path = overlay_path
        #: ids where an overlay row sits on top of a CONFIRMED skill entry -- the
        #: state the documented promotion step passes through, and the state a
        #: skill upgrade creates on a workspace that had already discovered the
        #: source.  Reported, never silently obeyed.
        self.shadowed: tuple[str, ...] = tuple(shadowed)

    # -- loading -------------------------------------------------------
    @classmethod
    def load(cls, workspace: Any | None = None) -> "Registry":
        """Skill registry, then the workspace overlay (overlay wins field-by-field)."""
        skill_path = skill_registry_path()
        merged: dict[str, dict] = {}
        origin: dict[str, str] = {}
        for sid, body in _read_registry_file(skill_path).items():
            merged[sid] = dict(body)
            origin[sid] = "skill"
        overlay_path = None
        shadowed: list[str] = []
        if workspace is not None:
            overlay_path = _root_of(workspace) / OVERLAY_FILE
            for sid, body in _read_registry_file(overlay_path).items():
                base = dict(merged.get(sid) or {})
                curated = bool(base.get("confirmed"))
                for key, value in (body or {}).items():
                    if value is not None:
                        base[key] = value
                if curated:
                    # `confirmed` is ASYMMETRIC: the overlay is always written
                    # with confirmed: false, so letting it win would demote a
                    # curated skill entry forever -- and silently, since
                    # `unconfirmed()` would then list a source the skill vouches
                    # for. A skill `true` is a fact the overlay cannot retract.
                    base["confirmed"] = True
                    shadowed.append(sid)
                merged[sid] = base
                origin[sid] = "overlay"
        entries = {sid: RegistryEntry.from_dict(sid, body, origin.get(sid, "skill"))
                   for sid, body in merged.items()}
        return cls(entries, skill_path=skill_path, overlay_path=overlay_path,
                   shadowed=sorted(shadowed))

    # -- lookups -------------------------------------------------------
    def get(self, source_id: str) -> RegistryEntry | None:
        return self.entries.get(source_id)

    def by_sha(self, sha: str) -> RegistryEntry | None:
        sha = (sha or "").strip().lower()
        if len(sha) != 64:
            return None
        for entry in self.entries.values():
            if entry.sha256 == sha:
                return entry
        return None

    def by_sha_prefix(self, prefix: str) -> list[RegistryEntry]:
        """Entries a TRUNCATED sha from repo prose could name.

        A prefix is a candidate, never an identity: `64498329…` in a CLAUDE.md
        resolves only through this list plus a real fingerprint.
        """
        prefix = (prefix or "").strip().lower()
        if len(prefix) < 6:
            return []
        out = []
        for entry in self.entries.values():
            known = [entry.sha256 or ""] + list(entry.aliases.get("sha256_prefixes") or [])
            if any(k and (k.startswith(prefix) or prefix.startswith(k)) for k in known):
                out.append(entry)
        return out

    def by_url(self, url: str) -> RegistryEntry | None:
        """Match an EDITION-specific URL only.

        ``book_page_url`` and ``errata_url`` are properties of the WORK -- both Tao
        editions carry the same author page -- so matching on them would resolve a
        link to whichever edition happened to be first in the file.
        """
        url = _norm_url(url)
        if not url:
            return None
        for entry in self.entries.values():
            candidates = [entry.download_url] + list(entry.aliases.get("urls") or [])
            if any(_norm_url(c) == url for c in candidates if c):
                return entry
        return None

    def by_filename(self, basename: str) -> list[RegistryEntry]:
        base = Path(str(basename or "")).name.lower()
        if not base:
            return []
        return [e for e in self.entries.values()
                if base in {str(f).lower() for f in (e.aliases.get("filenames") or [])}]

    def by_title(self, title: str, *, edition: str = "", isbn: str = "") -> list[RegistryEntry]:
        """Entries whose title (or a title alias) matches; ISBN narrows, edition ranks."""
        want = _norm_title(title)
        isbn_digits = _norm_isbn(isbn)
        hits: list[tuple[int, RegistryEntry]] = []
        for entry in self.entries.values():
            titles = {_norm_title(entry.title)}
            titles |= {_norm_title(t) for t in (entry.aliases.get("titles") or [])}
            title_hit = bool(want) and want in titles
            isbn_hit = bool(isbn_digits) and _norm_isbn(entry.isbn or "") == isbn_digits
            if not (title_hit or isbn_hit):
                continue
            score = (2 if isbn_hit else 0) + (1 if title_hit else 0)
            if edition and _edition_tokens(edition) & _edition_tokens(entry.edition):
                score += 4
            hits.append((score, entry))
        hits.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [e for _, e in hits]

    def same_work(self, source_id: str) -> list[RegistryEntry]:
        entry = self.get(source_id)
        if entry is None:
            return []
        return [self.entries[sid] for sid in entry.same_work if sid in self.entries]

    def page_shift(self, frm: str, to: str, item: str | None = None) -> int | None:
        """``to_page = frm_page + shift`` per ``page_map_to``; None when unmapped."""
        entry = self.get(frm)
        if entry is None:
            return None
        body = (entry.page_map_to or {}).get(to)
        if not isinstance(body, dict):
            return None
        if item and item in (body.get("exceptions") or []):
            return int(body.get("exception_shift") or 0)
        return int(body.get("default_shift") or 0)

    def unconfirmed(self) -> list[RegistryEntry]:
        return [e for e in self.entries.values() if not e.confirmed]

    # -- writing (overlay only) ----------------------------------------
    def append_overlay(self, workspace: Any, entry: RegistryEntry) -> Path:
        """Write ``confirmed: false`` into ``<workspace>/registry.yaml``.

        NEVER writes the skill's registry/sources.yaml: that file is curated by
        hand, and a command that could write it would turn a guess into a fact
        for every future workspace.
        """
        path = _root_of(workspace) / OVERLAY_FILE
        data = read_yaml(path) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
        data["version"] = REGISTRY_VERSION
        sources = data.setdefault("sources", {})
        if not isinstance(sources, dict):
            raise CfError(f"{path}: `sources:` must be a map keyed by source_id")
        body = entry.to_dict()
        body["confirmed"] = False
        body["added"] = date.today().isoformat()
        sources[entry.id] = body
        atomic_write(path, OVERLAY_HEADER + dump_yaml(data))
        self.entries[entry.id] = RegistryEntry.from_dict(entry.id, body, "overlay")
        return path

    def update_overlay(self, workspace: Any, source_id: str, **fields) -> Path | None:
        """Write back what preparing the source TAUGHT us (R3's other half).

        The overlay is meant to remember "what was learned about each known
        source"; a row written before the PDF was read knows none of it.  After
        `cf onboard` / `cf sources add` has fingerprinted the file, inferred the
        page offset from its running heads, read the item kinds off its headings
        and confirmed an errata page, those facts belong in the row -- otherwise
        promoting it into the skill registry by hand yields an entry that teaches
        the next discovery nothing, and a sha-less row cannot even be matched by
        content next time.  Returns None when this id is not in the overlay.
        """
        path = _root_of(workspace) / OVERLAY_FILE
        if not path.exists():
            return None
        data = read_yaml(path)
        if not isinstance(data, dict):
            return None
        sources = data.get("sources")
        if not isinstance(sources, dict) or source_id not in sources:
            return None
        body = dict(sources[source_id] or {})
        changed = False
        for key, value in fields.items():
            if value in (None, "", (), []):
                continue
            value = list(value) if isinstance(value, tuple) else value
            if body.get(key) != value:
                body[key] = value
                changed = True
        if not changed:
            return path
        body["confirmed"] = False        # still nobody's word but ours
        sources[source_id] = body
        data["version"] = REGISTRY_VERSION
        atomic_write(path, OVERLAY_HEADER + dump_yaml(data))
        self.entries[source_id] = RegistryEntry.from_dict(source_id, body, "overlay")
        return path

    # -- validation ----------------------------------------------------
    def validate(self) -> list[str]:
        """Structural problems, including page_map_to symmetry."""
        problems: list[str] = []
        for sid in self.shadowed:
            problems.append(
                f"{sid}: the workspace overlay shadows a CONFIRMED entry in the skill's "
                f"registry -- delete that row from {OVERLAY_FILE} so the curated one is used")
        for sid, entry in sorted(self.entries.items()):
            if not entry.title:
                problems.append(f"{sid}: title is required")
            if not entry.edition:
                problems.append(f"{sid}: edition is required")
            if entry.sha256 and (len(entry.sha256) != 64
                                 or any(c not in "0123456789abcdef" for c in entry.sha256)):
                problems.append(f"{sid}: sha256 must be 64 lowercase hex characters")
            fetch = str((entry.licence or {}).get("fetch") or "none")
            if fetch not in {"none", "author_site", "open"}:
                problems.append(f"{sid}: licence.fetch must be none|author_site|open")
            if (entry.licence or {}).get("redistribute"):
                problems.append(f"{sid}: licence.redistribute must be false (nothing is redistributed)")
            for other, body in (entry.page_map_to or {}).items():
                back = self.entries.get(other)
                if back is None:
                    problems.append(f"{sid}: page_map_to names unknown source {other}")
                    continue
                mirror = (back.page_map_to or {}).get(sid)
                if not isinstance(mirror, dict):
                    problems.append(f"{sid} -> {other} has no return page_map_to on {other}")
                    continue
                for key in ("default_shift", "exception_shift"):
                    a = int(body.get(key) or 0)
                    b = int(mirror.get(key) or 0)
                    if a != -b:
                        problems.append(
                            f"{sid} -> {other} {key} {a} is not the negative of "
                            f"{other} -> {sid} {key} {b}")
                if not (body.get("exceptions") or body.get("note")):
                    problems.append(
                        f"{sid} -> {other}: an empty `exceptions` list needs a `note` saying "
                        "which items shift")
            for other in entry.same_work:
                if other not in self.entries:
                    problems.append(f"{sid}: same_work names unknown source {other}")
                elif sid not in (self.entries[other].same_work or ()):
                    problems.append(f"{sid}: same_work {other} does not name {sid} back")
        return problems


# --------------------------------------------------------------------------
# source_id derivation (S3)
# --------------------------------------------------------------------------

_TITLE_STOPWORDS = frozenset({"a", "an", "the", "of", "to", "on", "in", "for", "and"})
#: generic mathematical nouns the rest of the title already implies
_TITLE_GENERIC = frozenset({"introduction", "course", "first", "second", "lectures", "notes"})
#: dropped only when a content word survives without it
_TITLE_GENERIC_IF_OTHER = frozenset({"theory"})
_SERIES_TOKENS = frozenset({"gsm", "ams", "crc", "springer", "2e", "3e"})
_EDITION_WORDS = frozenset({"draft", "preprint", "published", "revised", "corrected", "final"})

_MAX_ID_LEN = 48


def _ascii(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in folded if not unicodedata.combining(c))


def slugify(text: str, *, max_words: int = 3) -> str:
    """Lowercase ASCII words joined by ``-`` (at most ``max_words``)."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", _ascii(text).lower()) if w]
    return "-".join(words[:max_words])


def _author_token(authors: Sequence[str]) -> str:
    if not authors:
        return ""
    first = str(authors[0] or "").strip()
    if not first:
        return ""
    if "," in first:                       # "Tao, Terence"
        surname = first.split(",", 1)[0]
    else:
        surname = first.split()[-1]
    return slugify(surname, max_words=1)


def _title_token(title: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", _ascii(title).lower()) if w]
    if words and words[0] in {"a", "an", "the"}:
        words = words[1:]
    words = [w for w in words if w not in _TITLE_STOPWORDS]
    # Drop the words that say nothing BEFORE taking the first two, not after.
    # Truncating first threw away the distinctive word: "Elementary Notes on
    # Sets" kept only `elementary` (because `notes` used up the second slot and
    # was then dropped), and "A First Course in Real Analysis" kept only `real`.
    # A source id is the primary key of every item, finding and pair, so a
    # non-distinctive one invites exactly the collision the `-2` suffix exists
    # to paper over.
    meaningful = [w for w in words if w not in _TITLE_GENERIC]
    if any(w not in _TITLE_GENERIC_IF_OTHER for w in meaningful):
        meaningful = [w for w in meaningful if w not in _TITLE_GENERIC_IF_OTHER]
    kept = meaningful[:2] or words[:1]
    return "-".join(kept)


def _edition_token(edition: str) -> str:
    """The first two edition-bearing tokens: ``gsm126-published``, ``2011-draft``.

    A bare digit run qualifies only as a 4-digit year or when it follows a series
    token (``GSM 126`` -> ``gsm126``), so ``2011-01-24 draft`` gives ``2011-draft``
    and not ``2011-01``.
    """
    raw = [w for w in re.split(r"[^A-Za-z0-9]+", _ascii(edition).lower()) if w]
    out: list[str] = []
    i = 0
    while i < len(raw) and len(out) < 2:
        tok = raw[i]
        if tok in _SERIES_TOKENS:
            if i + 1 < len(raw) and tok.isalpha() and raw[i + 1].isdigit():
                out.append(tok + raw[i + 1])
                i += 2
                continue
            if not tok.isalpha():            # "2e"/"3e": the token IS the edition
                out.append(tok)
            # ...but a bare series WORD says nothing: "AMS GSM 126" must give
            # `gsm126`, not `ams`, or every AMS book shares an id.
            i += 1
            continue
        if re.fullmatch(r"(19|20)\d{2}", tok):
            out.append(tok)
        elif tok in _EDITION_WORDS:
            out.append(tok)
        i += 1
    return "-".join(out)


def _edition_tokens(edition: str) -> set[str]:
    return {w for w in re.split(r"[^A-Za-z0-9]+", _ascii(edition).lower()) if w}


def source_id_for(title: str, edition: str = "", authors: Sequence[str] = (),
                  sha256: str | None = None, taken: Container[str] = ()) -> str:
    """A short, stable, human-legible slug: ``<author>-<title>-<edition>``.

    ``An Introduction to Measure Theory`` + ``AMS GSM 126, ... (published ...)``
    + ``Terence Tao`` -> ``tao-measure-gsm126-published``.

    Collisions never silently reuse an id: ``-2``, ``-3``, ... and, when no
    edition token survived, the first 8 hex of the sha instead.
    """
    parts = [p for p in (_author_token(authors), _title_token(title), _edition_token(edition)) if p]
    base = re.sub(r"-{2,}", "-", "-".join(parts)).strip("-")
    if not base:
        base = (sha256 or "")[:8] or "source"
    if len(base) > _MAX_ID_LEN:
        cut = base[:_MAX_ID_LEN]
        base = cut.rsplit("-", 1)[0] if "-" in cut else cut
    if base not in taken:
        return base
    if not _edition_token(edition) and sha256:
        candidate = f"{base}-{sha256[:8]}"
        if candidate not in taken:
            return candidate
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    raise CfError(f"cannot derive a free source_id from {base!r}")  # pragma: no cover


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _strs(value) -> tuple[str, ...]:
    if value in (None, "", {}):
        return ()
    if isinstance(value, (str, bytes)):
        return (str(value),)
    return tuple(str(v) for v in value)


def _norm_url(url: str | None) -> str:
    s = str(url or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    return s.rstrip("/")


def _norm_title(title: str) -> str:
    return " ".join(w for w in re.split(r"[^a-z0-9]+", _ascii(title).lower()) if w)


def _norm_isbn(isbn: str) -> str:
    return re.sub(r"[^0-9xX]", "", str(isbn or "")).lower()


def _read_registry_file(path: Path) -> dict[str, dict]:
    if not path or not Path(path).exists():
        return {}
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise CfError(f"{path}: expected a mapping at the top level")
    version = data.get("version")
    if version not in (None, REGISTRY_VERSION):
        raise CfError(f"{path}: registry version {version!r} (this skill writes {REGISTRY_VERSION})")
    sources = data.get("sources") or {}
    if not isinstance(sources, dict):
        raise CfError(f"{path}: `sources:` must be a map keyed by source_id")
    return {str(k): (v or {}) for k, v in sources.items()}


def entry_from_claim(source_id: str, claim, *, pdf=None) -> RegistryEntry:
    """Build an UNCONFIRMED registry entry from a discovery claim.

    Used by `cf onboard` for a source no registry knew about: it goes into the
    workspace overlay with ``confirmed: false`` and `cf status` says so.
    """
    licence = {"note": getattr(claim, "licence", "") or "", "fetch": "none", "redistribute": False}
    aliases: dict[str, list[str]] = {
        "sha256_prefixes": [p for p in [getattr(claim, "sha256_prefix", "")] if p],
        "urls": list(getattr(claim, "urls", ()) or ()),
        "filenames": sorted({Path(h).name for h in (getattr(claim, "pdf_hints", ()) or ())
                             if str(h).lower().endswith(".pdf")}),
        "titles": [t for t in [getattr(claim, "title", "")] if t],
    }
    if pdf is not None and getattr(pdf, "path", None) is not None:
        name = Path(pdf.path).name
        if name not in aliases["filenames"]:
            aliases["filenames"].append(name)
    return RegistryEntry(
        id=source_id,
        title=getattr(claim, "title", "") or "",
        authors=tuple(getattr(claim, "authors", ()) or ()),
        # "unspecified" rather than "": the workspace records an edition for every
        # source, and an overlay row that omits it is a row `Registry.validate`
        # (and now `cf status`) reports as broken on every run.
        edition=getattr(claim, "edition", "") or "unspecified",
        isbn=(getattr(claim, "isbn", "") or None),
        sha256=(getattr(pdf, "sha256", None) if pdf is not None else None)
               or (getattr(claim, "sha256", "") or None),
        bytes=(getattr(pdf, "bytes", None) if pdf is not None else getattr(claim, "bytes", None)),
        pages=(getattr(pdf, "pages", None) if pdf is not None else None),
        download_url=None,
        book_page_url=(getattr(claim, "book_page_url", "") or None),
        errata_url=None,
        licence=licence,
        offset=None,
        offset_confidence=None,
        offset_method="unknown",
        kinds=(),
        same_work=(),
        page_map_to={},
        aliases=aliases,
        confirmed=False,
        notes="discovered by `cf onboard`; nobody has checked it",
        origin="overlay",
    )
