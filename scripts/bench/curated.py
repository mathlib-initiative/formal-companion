"""The curated lists -- Mathlib's 1000.yaml and 100.yaml, and plain QID lists -- read one way.

New code, implementing docs/proposals/wikipedia-locate.md sections 8.1 (G1's
curated lists) and 8.5 step 1 (the frame's curated stratum, "Tier A ∩ curated
QIDs") in the shapes references/locate-bench.md sections 2 and 4 fix.  ``cf bench
build`` (the stratum) and ``cf bench anchor`` (G1) both read the lists here, so
an article the build counts as curated is one the anchor looks for a curated
declaration on, and the other way round.

* **1000.yaml** maps a Wikidata QID to ``{title, decl | decls, ...}``.  A key
  may carry one capital-letter suffix (``Q180345X``, ``Q4724004A``) that tells
  two entries about one item apart: the entry's QID is the key without it, and
  the key itself stays the entry's name.  Any other key is refused by name.
* **100.yaml** maps a theorem number to ``{title, decl | decls, ...}`` and names
  no QID.  An entry resolves to QIDs, in this order: its own ``wikidata`` field;
  a declaration it shares with a 1000.yaml entry read in the same call; its
  folded title equal to a 1000.yaml entry's.  Whether or not it resolves, it
  also matches an article whose folded title (:func:`fold_title`) is its own.
  An entry that matches no article is reported by number and title, never
  dropped silently.  Nothing here reaches the network: a title only an
  encyclopedia's redirects could resolve is the operator's to supply, as a QID
  in a plain list.
* **A plain list** holds one QID per line (``#`` comments), or a JSON list of
  QIDs or of ``{"qid"}`` rows; a value that is not a QID is reported in the
  file's ``rejected`` list, never read as one.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from cflib.errors import CfError
from cflib.yamlio import load_yaml

from . import inputs

QID = re.compile(r"Q[1-9][0-9]*\Z")
#: a 1000.yaml key: a QID, optionally with one capital letter telling two entries apart
KEY_1000 = re.compile(r"(Q[1-9][0-9]*)([A-Z]?)\Z")
#: a 100.yaml key: the theorem's number
KEY_100 = re.compile(r"[1-9][0-9]*\Z")

#: the two Mathlib lists, by their ``cflib.vocab.CANDIDATE_VIA`` names (G1 rows carry them as ``via``)
LIST_1000 = "mathlib_1000"
LIST_100 = "mathlib_100"
#: a plain QID list: a frame-side label only, never a G1 ``via``
LIST_QIDS = "qid_list"

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‛": "'", "′": "'",
                         "“": '"', "”": '"', "‟": '"', "″": '"'})
_DASHES = re.compile("[‐-―−]")


def fold_title(title: Any) -> str:
    """A title for matching only (never stored as one).

    NFKD with the combining marks dropped, curly quotes straightened, every
    dash a hyphen, underscores spaces, case folded, runs of space one, and a
    leading "the " dropped: ``The Cauchy-Schwarz Inequality`` and
    ``Cauchy–Schwarz inequality``, or ``L’Hôpital’s Rule`` and ``L'Hôpital's
    rule``, fold to one string.
    """
    text = unicodedata.normalize("NFKD", str(title or "")).translate(_QUOTES)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _DASHES.sub("-", text).replace("_", " ")
    text = " ".join(text.casefold().split())
    return text[4:] if text.startswith("the ") else text


def qid_of_1000_key(key: Any) -> str | None:
    """The QID a 1000.yaml key names (its suffix letter dropped), or None."""
    match = KEY_1000.match(str(key).strip())
    return match.group(1) if match else None


def declarations_of(entry: dict) -> list[str]:
    """``decl`` and ``decls`` of one curated entry, in order, each once."""
    out = []
    if isinstance(entry.get("decl"), str):
        out.append(entry["decl"])
    for name in entry.get("decls") or []:
        if isinstance(name, str):
            out.append(name)
    return list(dict.fromkeys(n.strip() for n in out if n and n.strip()))


@dataclass
class Entry:
    """One curated claim: the list it is from, its key, its QIDs and its declarations."""

    list: str
    key: str
    title: str | None
    declarations: list[str]
    qids: list[str] = field(default_factory=list)
    #: how the QIDs were found ("" when the entry matches by title alone)
    method: str = ""

    @property
    def matches_by_title(self) -> bool:
        return self.list == LIST_100 and bool(fold_title(self.title))

    @property
    def label(self) -> str:
        return f"{self.list} {'#' if self.list == LIST_100 else ''}{self.key}"


@dataclass
class CuratedLists:
    """Every curated list one command was given, read and resolved once."""

    entries: list[Entry] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    #: ``(by QID, by folded title)`` entry indexes; built on the first match, after resolution
    _by: tuple | None = field(default=None, init=False, repr=False, compare=False)

    @classmethod
    def from_qids(cls, qids: Iterable[str]) -> "CuratedLists":
        return cls(entries=[Entry(LIST_QIDS, q, None, [], [q], "listed") for q in sorted(set(qids))])

    @property
    def qids(self) -> set[str]:
        return {q for e in self.entries for q in e.qids}

    def matches(self, article: dict) -> list[Entry]:
        """The entries that name this article: by its QID, or (100.yaml) by its title."""
        by_qid, by_title = self._index()
        qid = article.get("wikidata")
        title = fold_title(article.get("title"))
        hits = set(by_qid.get(qid, ()) if qid else ()) | set(by_title.get(title, ()) if title else ())
        return [self.entries[i] for i in sorted(hits)]

    def _index(self) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        """``({qid: [entry index]}, {folded title: [entry index]})`` over the read lists."""
        if self._by is None:
            by_qid: dict[str, list[int]] = {}
            by_title: dict[str, list[int]] = {}
            for i, e in enumerate(self.entries):
                for q in e.qids:
                    by_qid.setdefault(q, []).append(i)
                if e.matches_by_title:
                    by_title.setdefault(fold_title(e.title), []).append(i)
            self._by = (by_qid, by_title)
        return self._by

    def unmatched(self, articles: Iterable[dict], lists: Iterable[str] = (LIST_100,)) -> list[dict]:
        """Entries of these lists that name none of ``articles``, by key and title."""
        wanted = set(lists)
        qids = {a.get("wikidata") for a in articles if a.get("wikidata")}
        titles = {fold_title(a.get("title")) for a in articles} - {""}
        return [{"list": e.list, "key": e.key, "title": e.title, "qids": e.qids}
                for e in self.entries if e.list in wanted
                and not (set(e.qids) & qids)
                and not (e.matches_by_title and fold_title(e.title) in titles)]

    def of_list(self, name: str) -> list[Entry]:
        return [e for e in self.entries if e.list == name]


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def _load(path: Path) -> Any:
    """The parsed document: a mapping, a list, or the text lines of a plain list."""
    path = Path(path)
    if not path.is_file():
        raise CfError(f"no such curated list: {path}")
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except ValueError:
            try:
                return [json.loads(line) for line in stripped.splitlines() if line.strip()]
            except ValueError as exc:
                raise CfError(f"{path}: neither JSON nor JSON lines ({exc})") from exc
    lines = [line.split("#", 1)[0].rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]
    if lines and not lines[0].lstrip().startswith("- ") and ":" in lines[0]:
        return load_yaml(text, where=str(path))
    return [line.strip()[2:].strip() if line.strip().startswith("- ") else line.strip()
            for line in lines]


def _kind_of_mapping(path: Path, data: dict) -> str:
    keys = [str(k).strip() for k in data]
    if any(KEY_1000.match(k) for k in keys):
        bad = [k for k in keys if not KEY_1000.match(k)]
        if bad:
            raise CfError(f"{path}: a QID-keyed list (1000.yaml) whose key {bad[0]!r} is not a "
                          f"Wikidata QID (a capital-letter suffix such as Q180345X is one)")
        return LIST_1000
    if any(KEY_100.match(k) for k in keys):
        bad = [k for k in keys if not KEY_100.match(k)]
        if bad:
            raise CfError(f"{path}: a numbered list (100.yaml) whose key {bad[0]!r} is not a number")
        return LIST_100
    raise CfError(f"{path}: a mapping keyed neither by Wikidata QID (1000.yaml) nor by theorem "
                  f"number (100.yaml); its first key is {keys[0]!r}" if keys else
                  f"{path}: an empty mapping")


def _entries_1000(data: dict) -> list[Entry]:
    out = []
    for key in sorted(data, key=str):
        entry = data[key] if isinstance(data[key], dict) else {}
        k = str(key).strip()
        out.append(Entry(LIST_1000, k, entry.get("title"), declarations_of(entry),
                         [qid_of_1000_key(k)], "key"))
    return out


def _entries_100(data: dict) -> list[Entry]:
    out = []
    for key in sorted(data, key=lambda k: int(str(k).strip())):
        entry = data[key] if isinstance(data[key], dict) else {}
        own = str(entry.get("wikidata") or "").strip()
        out.append(Entry(LIST_100, str(key).strip(), entry.get("title"), declarations_of(entry),
                         [own] if QID.match(own) else [], "wikidata field" if QID.match(own) else ""))
    return out


def _entries_qids(path: Path, values: list[Any]) -> tuple[list[Entry], list[str]]:
    """A plain list's QIDs, and every value that is not one."""
    out, rejected = [], []
    for value in values:
        token = str(value.get("qid") if isinstance(value, dict) else value).strip()
        if QID.match(token):
            out.append(Entry(LIST_QIDS, token, None, [], [token], "listed"))
        else:
            rejected.append(token)
    return out, rejected


def resolve_100(entries_100: list[Entry], entries_1000: list[Entry]) -> None:
    """Give each 100.yaml entry with no QID of its own the QIDs 1000.yaml settles (in place)."""
    by_decl: dict[str, set[str]] = {}
    by_title: dict[str, set[str]] = {}
    for e in entries_1000:
        for name in e.declarations:
            by_decl.setdefault(name, set()).update(e.qids)
        if fold_title(e.title):
            by_title.setdefault(fold_title(e.title), set()).update(e.qids)
    for e in entries_100:
        if e.qids:
            continue
        shared = sorted({q for name in e.declarations for q in by_decl.get(name, ())},
                        key=lambda q: int(q[1:]))
        if shared:
            e.qids, e.method = shared, "a declaration shared with 1000.yaml"
            continue
        same = sorted(by_title.get(fold_title(e.title), ()), key=lambda q: int(q[1:]))
        if same:
            e.qids, e.method = same, "the title of a 1000.yaml entry"


def read_lists(paths: Iterable[Path], *, kinds: Iterable[str] | None = None) -> CuratedLists:
    """Read every curated list, detect each one's kind, resolve 100.yaml through 1000.yaml.

    ``kinds`` (one per path) names each file's kind instead of detecting it --
    ``cf bench anchor --curated-1000/--curated-100`` know which file is which.
    """
    paths = [Path(p) for p in paths]
    given = list(kinds) if kinds is not None else [None] * len(paths)
    lists = CuratedLists()
    per_file: list[tuple[dict, list[Entry]]] = []
    for path, kind in zip(paths, given):
        data = _load(path)
        rejected: list[str] = []
        if isinstance(data, dict):
            detected = _kind_of_mapping(path, data)
            if kind and kind != detected:
                raise CfError(f"{path} is a {detected} list, not a {kind} one")
            entries = _entries_1000(data) if detected == LIST_1000 else _entries_100(data)
        elif isinstance(data, list):
            detected = LIST_QIDS
            if kind and kind != detected:
                raise CfError(f"{path} is a plain list of QIDs, not a {kind} mapping")
            entries, rejected = _entries_qids(path, data)
        else:
            raise CfError(f"{path}: a curated list is a mapping or a list")
        lists.entries.extend(entries)
        per_file.append(({"name": path.name, "sha256": inputs.sha256_file(path), "kind": detected,
                          "entries": len(entries), "rejected": rejected}, entries))
    resolve_100(lists.of_list(LIST_100), lists.of_list(LIST_1000))
    lists._by = None                       # the QIDs changed: index after resolution
    for record, entries in per_file:
        record["qids"] = len({q for e in entries for q in e.qids})
        if record["kind"] == LIST_100:
            record["resolved"] = {}
            for e in entries:
                if e.qids:
                    record["resolved"][e.method] = record["resolved"].get(e.method, 0) + 1
            record["title_only"] = sum(1 for e in entries if not e.qids)
        lists.files.append(record)
    return lists
