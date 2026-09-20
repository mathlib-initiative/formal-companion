"""The answer key: which defects a fixture carries, and which loci are clean.

New code.  A key is a file about ONE source, listing three sorts of entry:

``expect: "defect"``  a planted defect.  Not reporting it is a false negative.
``expect: "clean"``   a locus that carries no defect -- either a deliberate
                      decoy (a CONVENTION-only difference a worker is tempted
                      to file) or an ordinary faithful item.  A finding here is
                      a false positive, and the decoys are named so the score
                      can say which trap was walked into.

Every entry carries ``(source_id, repo, item, kind, decls)``: the first four are
the run-independent join key -- the same one ``cf score`` computes for a finding
-- and ``decls`` is evidence for a human, never part of the join.  A worker that
names the right item with the wrong declaration has found the defect; the score
records the disagreement rather than dropping the match.

The key is data about a FIXTURE.  Nothing here reads a real companion, and a
key that names an item its source does not have is a broken key, not a miss:
:func:`check_against_items` is what the fixture's own test uses to say so.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Sequence

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:  # pragma: no cover - import side effect
    sys.path.insert(0, str(_SCRIPTS))

from cflib import vocab  # noqa: E402
from cflib.errors import CfError  # noqa: E402
from cflib.jsonio import read_json  # noqa: E402

#: an entry says a defect is there, or says the locus is clean
EXPECT_VALUES = ("defect", "clean")
#: required on every entry
REQUIRED = ("repo", "item", "expect")


def load(path: str | Path) -> dict:
    """Read and validate an answer key; raises :class:`CfError` on a bad one."""
    p = Path(path)
    if not p.exists():
        raise CfError(f"no answer key at {p}",
                      hint="tests/e2e/answer-key.json is the one this repo ships")
    data = read_json(p)
    if not isinstance(data, dict):
        raise CfError(f"{p} is not an answer key object")
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise CfError(f"{p} lists no entries",
                      hint="an answer key is {'source_id': ..., 'entries': [...]}")
    default_source = str(data.get("source_id") or "")
    seen: set[tuple] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise CfError(f"{p}: entry {index} is not an object")
        for field in REQUIRED:
            if not str(entry.get(field) or "").strip():
                raise CfError(f"{p}: entry {index} has no {field}")
        entry.setdefault("source_id", default_source)
        if not entry["source_id"]:
            raise CfError(f"{p}: entry {index} names no source_id",
                          hint="set it on the entry or once at the top of the key")
        expect = entry["expect"]
        if expect not in EXPECT_VALUES:
            raise CfError(f"{p}: entry {index} has expect={expect!r}",
                          hint="expect is 'defect' or 'clean'")
        kind = str(entry.get("kind") or "")
        if expect == "defect":
            if not kind:
                raise CfError(f"{p}: the planted defect at entry {index} names no kind",
                              hint="a defect is scored per kind; kinds: "
                                   + ", ".join(sorted(vocab.FINDING_KINDS)))
            vocab.check(kind, vocab.FINDING_KINDS, "finding kind")
        elif kind:
            raise CfError(f"{p}: entry {index} expects 'clean' but names kind {kind!r}",
                          hint="a clean locus must not be reported under ANY kind, so it "
                               "carries none")
        decls = entry.get("decls") or []
        if not isinstance(decls, list) or any(not isinstance(d, str) for d in decls):
            raise CfError(f"{p}: entry {index} has a malformed decls list",
                          hint="decls is a list of `<repo>:<declaration>` strings")
        ident = (entry["source_id"], entry["repo"], entry["item"], kind)
        if ident in seen:
            raise CfError(f"{p}: two entries for {ident}",
                          hint="one entry per (source, repo, item, kind): a second one would "
                               "be counted twice")
        seen.add(ident)
    return data


def entry_id(entry: dict) -> str:
    """A stable human id for one entry, used in the report and the tests."""
    return "{}/{}/{}/{}".format(entry.get("source_id", ""), entry.get("repo", ""),
                                entry.get("item", ""), entry.get("kind") or "clean")


def defects(key: dict) -> list[dict]:
    """The planted defects, in file order."""
    return [e for e in key["entries"] if e.get("expect") == "defect"]


def clean(key: dict) -> list[dict]:
    """The loci that must not be reported at all."""
    return [e for e in key["entries"] if e.get("expect") == "clean"]


def decoys(key: dict) -> list[dict]:
    """Clean loci that are there to be mistaken for defects."""
    return [e for e in clean(key) if e.get("class")]


def summary(key: dict) -> dict:
    """Counts for the score document's header."""
    by_kind: dict[str, int] = {}
    for entry in defects(key):
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
    return {
        "source_id": str(key.get("source_id") or ""),
        "entries": len(key["entries"]),
        "defects": len(defects(key)),
        "decoys": len(decoys(key)),
        "clean": len(clean(key)),
        "defects_by_kind": dict(sorted(by_kind.items())),
        "repos": sorted({str(e.get("repo") or "") for e in key["entries"]}),
    }


def check_against_items(key: dict, items: Iterable[str],
                        decls_by_repo: dict[str, Sequence[str]] | None = None) -> list[str]:
    """Problems with the key ITSELF, as prose lines; empty when it is sound.

    ``items`` are the source's item ids and ``decls_by_repo`` the declarations
    each companion actually carries.  A key that names an item the source does
    not print, or a declaration the companion does not have, scores a fixture
    that no longer exists -- which looks exactly like a recall failure.
    """
    known = {str(i) for i in items}
    problems: list[str] = []
    for entry in key["entries"]:
        if entry["item"] not in known:
            problems.append(f"{entry_id(entry)}: the source has no item {entry['item']!r}")
        if decls_by_repo is None:
            continue
        have = set(decls_by_repo.get(entry["repo"], ()))
        for decl in entry.get("decls") or []:
            name = decl.split(":", 1)[1] if ":" in decl else decl
            missing = name not in have
            # A missing_item entry is the one case where the declaration is
            # meant to be absent: the fixture removed its book tag.
            if entry.get("class") == "missing_item":
                if not missing:
                    problems.append(f"{entry_id(entry)}: {decl} is still tagged, so nothing "
                                    "is missing")
            elif missing:
                problems.append(f"{entry_id(entry)}: {entry['repo']} has no declaration {name!r}")
    return problems
