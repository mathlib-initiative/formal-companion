"""Ingest one library's probe index into a universe: a normalised, re-decided index.

New code for docs/proposals/wikipedia-locate.md sections 2.4 and 2.5, writing
the normalised index references/locate-documents.md section 8 fixes
(``corpora/<universe>/index/<library>/decls.jsonl`` + ``manifest.json``).

**The index must be the pinned one, whole.**  The library, the commit and the
toolchain are read from the index's OWN header and, where present, its
``manifest.json``; each must name the universe's pin, and the two may not
disagree, so a sibling manifest cannot vouch for another library's index.  A
probe index needs both files: the manifest's ``index_sha256``/``index_bytes``
must be the bytes ingested, the header's ``declaration_count`` and the
manifest's ``records`` the records read -- an index cut short at a line
boundary is refused, not pinned -- and a manifest saying the probed checkout
was ``dirty`` is refused.  Every module path null is refused too: that is a
probe that never saw its sources (a ``SRC_DIR`` that did not reach it).

**Streamed.**  A Mathlib index is hundreds of megabytes of JSON lines, so
nothing here loads a file whole: ``index.jsonl`` is read line by line (module
paths and the input order first, then the declarations), each declaration is
normalised as it passes, and the output is written as it goes.  The probe
writes declarations sorted by name; an input that is not sorted goes through
an external merge sort in temporary chunk files instead, so the output is
sorted by name whatever arrived.  Two PUBLIC declarations with one name in one
library are refused (the exact table keys on the name).  The probe names a
private declaration by its user name, so two modules' private helpers can
share one; such private records are dropped -- a private declaration is no
place a consumer can link to -- and counted by name in the manifest.

**Every proof status is re-decided** with ``cflib.vocab.decide_proof_status``,
never copied from the probe, because the probe the host ran decides in another
order: ``axiom`` (by kind) first, then ``wanted``, ``statement_only``,
``definition`` for data and ``proved`` otherwise; it has no ``axiom_dependent``,
emits no statement flag, and an ``isProp`` that failed falls through to
``proved``.  The inputs are what the probe emits, and a record missing one of
them is refused, never guessed:

* ``wanted`` -- the probe found a wrapper (``wrapper`` is not null);
* the closure -- ``nonstandard_axioms`` (required, a list): every axiom the
  declaration reaches except ``propext``, ``Classical.choice`` and
  ``Quot.sound``, ``sorryAx`` included.  An axiom declaration's closure
  contains the axiom itself (the probe's node contributes it), so a
  non-standard axiom is ``axiom_dependent`` -- ``collectAxioms`` semantics;
* ``is_prop`` -- the probe's own data/proof split: ``proved`` means the type is
  a proposition, ``definition`` that it is data; theorems are propositions and
  inductive types are data by kind; of the three standard axioms only
  ``Classical.choice`` is data.  When the probe's ``isProp`` call failed the
  answer is unknown, and a declaration whose status would turn on it is given
  the conservative ``statement_only`` -- it can make no passage ``formalized``
  -- with ``is_prop_unknown`` in its ``flags``, counted in the manifest.

The probe's own answer must agree with its fields (``wanted`` exactly when a
wrapper was found, ``statement_only`` exactly when ``sorryAx`` is in the
closure, ``axiom`` exactly for an axiom); a record that contradicts itself is
refused, because re-deciding it would move it silently.

**The statement flag** (a ``Prop``-valued definition, section 2.4) is a
fallback: the probe emits no such field, so it is derived from the kind (a
``def``, ``abbrev`` or ``opaque``) and the printed type, whose final codomain
-- after the last top-level ``→`` -- is ``Prop``.  A probe field computed from
the type itself (``isPropFormerType``) would be exact; that is an open
question for the template, which is not changed here.

**Not implemented** (TODO): section 2.4's rule 1 also asks for a SOURCE SCAN
recording a ``{module, lines}`` end with status ``wanted`` where a toolchain's
``proof_wanted`` adds no constant.  Nothing scans for it yet, so such a result
is invisible to the index; the manifest says ``proof_wanted_scan: "not
implemented"`` so no reader takes its absence for a check.

**Tags** (``cflib.vocab.TAG_KINDS``): ``@[wikidata Q…]``, ``@[stacks …]`` and
``@[kerodon …]`` attributes in the declaration's source span, read from a
library checkout -- which must be at the universe's pinned commit and clean,
read as plain files that may not leave it, nothing in it ever run -- and
Wikipedia URLs in the docstring, stored in one canonical form
(:func:`wikipedia_urls`).

**Published indexes** (``autoform-library-index/v0``, an index a library
publishes itself) were not produced by this probe, so its canary says nothing
about their statuses and a status word without a closure cannot be re-decided.
They are refused unless the operator accepts one by name with a reason
(``accept_published``), which is recorded on the library's universe row; the
library, commit and toolchain checks apply to them as to a probe index.  A
published record's own status word is mapped (:func:`published_status`), never
guessed: Autoform's ``complete`` -- "rests on no sorry and no non-standard
axiom" -- is ``proved`` for a theorem and ``definition`` for a data kind, and its
kind word ``lemma`` is the probe's ``theorem``.

The acceptance canary (section 2.4) is ingested by :func:`check_canary`: its
four declarations must be classified as expected before any library on its
toolchain is ingested, and :func:`ingest` refuses a probe index whose
toolchain has no passed canary in ``universe.yaml``.
"""
from __future__ import annotations

import heapq
import json
import os
import re
import shutil
import tempfile
import urllib.parse
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Iterator

from cflib import gitsafe
from cflib import state as state_mod
from cflib import vocab
from cflib.errors import CfError
from cflib.hashing import sha256_file

from .universe import COMMIT, Universe, lean_version

INDEX_SCHEMA = "cf-library-index/1"
#: the probe's own schema (scripts/corpus/probe_merge.py) and the published base it extends
PROBE_SCHEMA = "formal-companion-library-index/v0"
PUBLISHED_SCHEMA = "autoform-library-index/v0"
#: the probe statuses ingest understands (scripts/corpus/probe_merge.py PROOF_STATUSES)
PROBE_STATUSES = frozenset({"axiom", "definition", "proved", "statement_only", "wanted"})
#: An ``autoform-library-index/v0`` status word, for a published record with no
#: closure, and the proof status it means.  Anything else is refused.
PUBLISHED_STATUSES = {
    "proved": vocab.PROOF_PROVED, "definition": vocab.PROOF_DEFINITION,
    "wanted": vocab.PROOF_WANTED, "statement_only": vocab.PROOF_STATEMENT_ONLY,
    "sorry": vocab.PROOF_STATEMENT_ONLY, "axiom_dependent": vocab.PROOF_AXIOM_DEPENDENT,
}
#: ``complete`` -- one of the only two status words Autoform's published index uses
#: (``complete`` and ``wanted``) -- says a declaration rests on no ``sorry`` and no
#: non-standard axiom, and nothing about whether it is a proof or data: that is its
#: kind's.  A theorem is ``proved``; a data kind is a ``definition``.  An instance is
#: data here, though an instance of a ``Prop``-valued class (``Fact p``) is a proof: the
#: published record carries no type to tell them apart, and either way no instance
#: formalizes a theorem passage.  ``complete`` on any other kind (an ``axiom``) is refused.
PUBLISHED_COMPLETE = "complete"
PUBLISHED_COMPLETE_BY_KIND = {
    "theorem": vocab.PROOF_PROVED,
    **{kind: vocab.PROOF_DEFINITION
       for kind in ("def", "abbrev", "opaque", "instance", "inductive", "structure", "class")},
}
#: kind words of a published index and the probe's kind each is (``lemma`` is
#: Autoform's word for what Lean and the probe call a ``theorem``)
PUBLISHED_KINDS = {"lemma": "theorem"}
DATA_KINDS = frozenset({"inductive", "structure", "class"})
DEFINITION_KINDS = frozenset({"def", "abbrev", "opaque"})
#: what each record flag (``cflib.vocab.INDEX_FLAGS``) means, for the command's summary line
FLAG_MEANINGS = {
    vocab.INDEX_FLAG_IS_PROP_UNKNOWN: "the probe's isProp failed, so the status is the "
                                      "conservative statement_only",
    vocab.INDEX_FLAG_PUBLISHED_STATUS: "the status is the publishing library's own word, "
                                       "accepted on trust with --accept-published, not "
                                       "re-decided from a closure",
}
#: records per in-memory chunk when the input has to be sorted
CHUNK_RECORDS = 50_000
#: source files kept open at once while tags are read from a checkout
_FILE_CACHE = 256
#: names of dropped private collisions kept in the manifest
_NAMED = 20

#: The acceptance canary's four declarations (templates/corpus/canary/), and what
#: each must be decided as before a toolchain's libraries are ingested.
CANARY = {
    "CfCanary.hiddenSorry": {"proof_status": vocab.PROOF_STATEMENT_ONLY, "statement": False},
    "CfCanary.wantedTheorem": {"proof_status": vocab.PROOF_WANTED, "statement": False},
    "CfCanary.propDef": {"proof_status": vocab.PROOF_DEFINITION, "statement": True},
    "CfCanary.axiomTheorem": {"proof_status": vocab.PROOF_AXIOM_DEPENDENT, "statement": False},
}

_TAG_ATTR = {
    "wikidata": re.compile(r"\bwikidata\s+(Q[1-9][0-9]*)\b"),
    "stacks": re.compile(r"\bstacks\s+([0-9A-Z]{4})\b"),
    "kerodon": re.compile(r"\bkerodon\s+([0-9A-Z]{4})\b"),
}
_ATTRIBUTES = re.compile(r"@\[(.*?)\]", re.S)
#: where a Wikipedia article URL starts: the language, the optional mobile host, /wiki/
_WIKIPEDIA = re.compile(r"https?://([a-z][a-z0-9-]{1,15})(?:\.m)?\.wikipedia\.org/wiki/",
                        re.IGNORECASE)
#: characters that end a URL in prose (RFC 3986 excludes them; markdown and Lean use them)
_URL_STOP = frozenset(' \t\r\n<>"`[]{}|\\^')
#: punctuation a sentence puts after a URL, stripped from its end
_URL_TRAILING = ".,;:!?'*"
_REF = re.compile(r"refs/[A-Za-z0-9._/-]+\Z")


# --------------------------------------------------------------------------
# deciding one declaration
# --------------------------------------------------------------------------

def _top_level_codomain(signature: str) -> str:
    """The text after the last ``→`` or ``,`` outside brackets: a type's final codomain."""
    depth = 0
    cut = 0
    for i, ch in enumerate(signature):
        if ch in "([{⦃⟨":
            depth += 1
        elif ch in ")]}⦄⟩":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "→,":
            cut = i + 1
    return signature[cut:].strip()


def statement_flag(kind: str, signature: str | None) -> bool:
    """A ``def``/``abbrev``/``opaque`` whose printed type ends in ``Prop`` (the fallback)."""
    if kind not in DEFINITION_KINDS or not signature:
        return False
    return _top_level_codomain(str(signature)) == "Prop"


def _is_prop_failed(errors) -> bool:
    return any(str(e).startswith("isProp") for e in (errors or []))


def _closure(record: dict) -> set[str]:
    """``nonstandard_axioms`` as a set; refused when absent, null or not a list of names."""
    value = record.get("nonstandard_axioms")
    if not isinstance(value, list) or not all(isinstance(a, str) and a for a in value):
        raise CfError(f"{record.get('name')}: nonstandard_axioms is {value!r}, not a list of "
                      f"axiom names",
                      hint="the probe always writes the closure; a record without it cannot "
                           "be re-decided, and is refused rather than read as closure-free")
    return set(value)


def check_consistent(record: dict) -> None:
    """Refuse a probe record whose own status contradicts its fields (the template's rules)."""
    name = record.get("name")
    kind = str(record.get("kind") or "")
    status = str(record.get("proof_status") or "")
    closure = _closure(record)
    wrapper = record.get("wrapper") is not None
    if (kind == "axiom") != (status == "axiom"):
        problem = f"kind {kind!r} with probe status {status!r}"
    elif kind != "axiom" and wrapper != (status == "wanted"):
        problem = (f"probe status {status!r} with wrapper {record.get('wrapper')!r}: the probe "
                   f"says wanted exactly when it found a wrapper")
    elif kind != "axiom" and not wrapper and \
            ("sorryAx" in closure) != (status == "statement_only"):
        problem = (f"probe status {status!r} with closure {sorted(closure)!r}: the probe says "
                   f"statement_only exactly when sorryAx is in the closure")
    elif kind == "theorem" and status == "definition":
        problem = "a theorem the probe called data"
    elif kind in DATA_KINDS and status == "proved":
        problem = f"a {kind} the probe called a proof"
    else:
        return
    raise CfError(f"{name}: the probe's record contradicts itself ({problem})",
                  hint="re-probe the library; a record that disagrees with itself cannot be "
                       "re-decided without guessing which half is wrong")


def decide(record: dict) -> tuple[str, bool, list[str]]:
    """``(proof_status, statement, flags)`` of one probe declaration, by section 2.4."""
    name = str(record.get("name") or "")
    kind = str(record.get("kind") or "")
    probe_status = str(record.get("proof_status") or "")
    closure = _closure(record)
    flags: list[str] = []
    if kind == "axiom" and name not in vocab.STANDARD_AXIOMS:
        closure.add(name)
    if kind == "theorem":
        is_prop: bool | None = True
    elif kind in DATA_KINDS:
        is_prop = False
    elif kind == "axiom":
        is_prop = name != "Classical.choice"
    elif probe_status == "proved":
        is_prop = True
    elif probe_status == "definition":
        is_prop = False
    else:
        is_prop = None
    if _is_prop_failed(record.get("errors")) and kind not in ("theorem", "axiom") \
            and kind not in DATA_KINDS:
        is_prop = None
    wanted = record.get("wrapper") is not None or probe_status == "wanted"
    status = vocab.decide_proof_status(wanted=wanted, axioms=closure, is_prop=bool(is_prop))
    if is_prop is None and status in (vocab.PROOF_PROVED, vocab.PROOF_DEFINITION):
        status = vocab.PROOF_STATEMENT_ONLY
        flags.append(vocab.INDEX_FLAG_IS_PROP_UNKNOWN)
    statement = statement_flag(kind, record.get("signature")) and status != vocab.PROOF_PROVED
    return status, statement, flags


def published_status(word: str, kind: str) -> str | None:
    """The proof status a published record's own status word means, for its kind; None if none.

    ``kind`` is the probe's word (``lemma`` already ``theorem``): ``complete`` is
    decided by it (:data:`PUBLISHED_COMPLETE_BY_KIND`), every other word by
    :data:`PUBLISHED_STATUSES` alone.
    """
    if word == PUBLISHED_COMPLETE:
        return PUBLISHED_COMPLETE_BY_KIND.get(kind)
    return PUBLISHED_STATUSES.get(word)


def _printable(record: dict) -> bool:
    if record.get("signature") is None:
        return False
    if "signature" in (record.get("truncated_fields") or []):
        return False
    return not any(str(e).startswith("signature") for e in (record.get("errors") or []))


# --------------------------------------------------------------------------
# tags
# --------------------------------------------------------------------------

def _wikipedia_links(text: str) -> Iterator[tuple[int, int, str | None]]:
    """``(start, end, canonical URL or None)`` of every Wikipedia article link in ``text``.

    ``end`` covers the whole link as written -- a fragment or query included --
    which is what :func:`strip_wikipedia_urls` removes; the canonical URL is
    what :func:`wikipedia_urls` keeps, None when the link names no title.
    """
    text = str(text or "")
    for match in _WIKIPEDIA.finditer(text):
        lang = match.group(1).lower()
        i, depth, opened = match.end(), 0, []
        title: list[str] = []
        while i < len(text):
            ch = text[i]
            if ch in _URL_STOP or ch in "#?":
                break
            if ch == "(":
                depth += 1
                opened.append(len(title))
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
                opened.pop()
            title.append(ch)
            i += 1
        end = i
        if i < len(text) and text[i] in "#?":
            # the fragment or query: no part of the title, part of the link
            while end < len(text) and text[end] not in _URL_STOP and text[end] != ")":
                end += 1
            while end > i + 1 and text[end - 1] in _URL_TRAILING:
                end -= 1                    # a sentence's punctuation after the fragment
        if depth:
            # an unclosed '(' is no part of a title: cut back to before it
            title = title[:opened[0]]
        raw = "".join(title).rstrip(_URL_TRAILING)
        if end == i:
            end = match.end() + len(raw)
        decoded = urllib.parse.unquote(raw, errors="strict") if "%" in raw else raw
        decoded = re.sub(r"[\s_]+", "_", decoded).strip("_")
        url = (f"https://{lang}.wikipedia.org/wiki/{decoded[0].upper()}{decoded[1:]}"
               if decoded else None)
        yield match.start(), end, url


def wikipedia_urls(text: str) -> list[str]:
    """Every Wikipedia article URL in ``text``, in one canonical form, sorted and distinct.

    A title runs to whitespace or a character no URL in prose carries (``<``,
    ``>``, a quote mark, a backtick, a bracket, ``|``); an apostrophe is part
    of it (``Wilson's_theorem``), and so is a BALANCED pair of parentheses
    (``Lagrange's_theorem_(group_theory)``) -- an unmatched ``)`` ends it, which
    is how a markdown link's own parenthesis is left out.  A fragment or a query
    ends the title and is dropped, and so is sentence punctuation at its end.
    The canonical form is the desktop page URL
    ``https://<lang>.wikipedia.org/wiki/<title>``: the mobile host folded, the
    title percent-decoded, spaces and runs of underscores one underscore, the
    first letter upper-case as MediaWiki stores it (references/locate-documents.md
    section 8) -- so the L2 join on the page URL is an exact one.
    """
    return sorted({url for _s, _e, url in _wikipedia_links(text) if url})


def strip_wikipedia_urls(text: str | None) -> str | None:
    """``text`` with every Wikipedia article link removed: the G1-blind database's docstrings."""
    if not text:
        return text
    out, cursor = [], 0
    for start, end, _url in _wikipedia_links(text):
        out.append(text[cursor:start])
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def checkout_head(root: Path) -> str | None:
    """The 40-hex commit a checkout's HEAD names, read from its files; None when unreadable.

    Plain reads of ``.git/HEAD`` (or a worktree's ``gitdir:`` file), the loose
    ref it points at and ``packed-refs``: no git is run.  A ref outside
    ``refs/`` or one that climbs out with ``..`` is not followed.
    """
    root = Path(root)
    dot = root / ".git"
    try:
        if dot.is_file():
            text = dot.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = (root / text[len("gitdir:"):].strip()).resolve()
        elif dot.is_dir():
            gitdir = dot
        else:
            return None
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if COMMIT.match(head):
            return head
        if not head.startswith("ref: "):
            return None
        ref = head[len("ref: "):].strip()
        if not _REF.match(ref) or ".." in ref:
            return None
        common = gitdir
        if (gitdir / "commondir").is_file():
            common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
        for base in (gitdir, common):
            loose = base / ref
            if loose.is_file():
                value = loose.read_text(encoding="utf-8").strip()
                return value if COMMIT.match(value) else None
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and COMMIT.match(parts[0]):
                    return parts[0]
    except (OSError, UnicodeDecodeError):
        return None
    return None


def check_checkout(checkout: Path, commit: str) -> None:
    """Refuse a checkout that is not at ``commit``, or not clean, before tags are read from it.

    Tags are read at the probe's line numbers, so a checkout at any other
    revision -- or with edited files -- moves a neighbour's attribute onto a
    declaration.  HEAD is read from files; cleanliness is ``git status`` through
    ``cflib.gitsafe``, which runs none of the repository's programs.
    """
    head = checkout_head(checkout)
    if head is None:
        raise CfError(f"{checkout} is not a git checkout whose HEAD can be read",
                      hint="--checkout names a clone of the library at the pinned commit")
    if head != commit:
        raise CfError(f"{checkout} is at {head[:12]}, not at the pinned {commit[:12]}",
                      hint="check out the pinned commit; tags are read at the probe's line numbers")
    try:
        got = gitsafe.run(checkout, ["status", "--porcelain", "--untracked-files=no"])
    except (OSError, ValueError) as exc:
        raise CfError(f"could not tell whether {checkout} is clean ({exc})") from None
    if got.returncode != 0:
        raise CfError(f"could not tell whether {checkout} is clean: "
                      f"{(got.stderr or '').strip()[:200]}")
    if got.stdout.strip():
        raise CfError(f"{checkout} has modified tracked files",
                      hint="tags must be read from the pinned tree; stash or reset the changes")


class _Checkout:
    """Plain reads of a library checkout's files, never leaving it, a few kept open."""

    def __init__(self, root: Path | None):
        self.root = Path(root).resolve() if root else None
        self._lines: "OrderedDict[str, list[str] | None]" = OrderedDict()
        self.unreadable: set[str] = set()

    def lines(self, rel: str | None) -> list[str] | None:
        if self.root is None or not rel:
            return None
        if rel in self._lines:
            self._lines.move_to_end(rel)
            return self._lines[rel]
        candidate = (self.root / rel).resolve()
        got: list[str] | None
        try:
            candidate.relative_to(self.root)
        except ValueError:
            got = None                     # a path that leaves the checkout is not read
        else:
            try:
                got = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                got = None
        if got is None:
            self.unreadable.add(rel)
        self._lines[rel] = got
        if len(self._lines) > _FILE_CACHE:
            self._lines.popitem(last=False)
        return got


def tags_of(record: dict, path: str | None, checkout: _Checkout) -> list[dict]:
    """The declaration's tags: attributes in its source span, Wikipedia URLs in its docstring."""
    found: set[tuple[str, str]] = set()
    lines = checkout.lines(path)
    start, name_line = record.get("start_line"), record.get("line")
    if lines and isinstance(start, int) and isinstance(name_line, int) and 1 <= start <= name_line:
        span = "\n".join(lines[start - 1:name_line])
        for attrs in _ATTRIBUTES.findall(span):
            for tag, pattern in _TAG_ATTR.items():
                for value in pattern.findall(attrs):
                    found.add((tag, value))
    for url in wikipedia_urls(record.get("docstring") or ""):
        found.add(("wikipedia", url))
    return [{"tag": tag, "value": value} for tag, value in sorted(found)]


# --------------------------------------------------------------------------
# reading a probe index
# --------------------------------------------------------------------------

def _records(path: Path) -> Iterator[tuple[int, dict]]:
    """``(line number, record)`` of every non-blank line, refusing a line that is not an object."""
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CfError(f"{path.name} line {line_no} is not JSON ({exc.msg})") from None
            if not isinstance(record, dict):
                raise CfError(f"{path.name} line {line_no} is not a JSON object")
            yield line_no, record


def _index_file(source: Path) -> Path:
    if source.is_file():
        return source
    for name in ("index.jsonl", "autoform-library-index.jsonl"):
        if (source / name).is_file():
            return source / name
    raise CfError(f"no index.jsonl in {source}",
                  hint="--from names the probe's output directory (index.jsonl + manifest.json)")


def read_header(index: Path) -> dict:
    for _line_no, record in _records(index):
        if record.get("record") != "header":
            raise CfError(f"{index.name} does not start with a header record")
        if record.get("schema") not in (PROBE_SCHEMA, PUBLISHED_SCHEMA):
            raise CfError(f"{index.name} has schema {record.get('schema')!r}, not "
                          f"{PROBE_SCHEMA} or {PUBLISHED_SCHEMA}")
        return record
    raise CfError(f"{index.name} is empty")


def _text(value, what: str, line_no: int) -> str | None:
    """A field that must be a NUL-free string or null."""
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value:
        raise CfError(f"line {line_no}: {what} is {value!r}, not a string without NUL")
    return value


def _scan(index: Path) -> dict:
    """One pass: module paths, record counts, and whether declarations arrive sorted by name."""
    paths: dict[str, str | None] = {}
    sorted_input, last = True, None
    declarations = modules = 0
    for line_no, record in _records(index):
        kind = record.get("record")
        if kind == "module":
            modules += 1
            name = _text(record.get("name"), "a module name", line_no)
            paths[str(name)] = _text(record.get("path") if "path" in record
                                     else record.get("source_file"), "a module path", line_no)
        elif kind == "declaration":
            declarations += 1
            name = str(record.get("name") or "")
            if last is not None and name < last:
                sorted_input = False
            last = name
    return {"paths": paths, "sorted": sorted_input, "declarations": declarations,
            "modules": modules}


def normalise(record: dict, module_paths: dict, checkout: _Checkout, *,
              published: bool, line_no: int = 0) -> dict:
    """One probe (or published) declaration as a ``cf-library-index/1`` record."""
    name = _text(record.get("name"), "the declaration name", line_no)
    if not name:
        raise CfError(f"line {line_no}: a declaration record has no name")
    kind = str(record.get("kind") or "")
    if published:
        kind = PUBLISHED_KINDS.get(kind, kind)
        if kind != record.get("kind"):
            record = {**record, "kind": kind}
    module = _text(record.get("module"), "the module", line_no)
    path = _text(record.get("path"), "the path", line_no) or module_paths.get(str(module))
    probe_status = str(record.get("proof_status") or "")
    if published and record.get("nonstandard_axioms") is None:
        # a published record carries its library's own status and no closure to
        # re-decide it from: take the status only when it is a word that means one of
        # ours, flagged, and refuse any other
        probe_status = str(record.get("proof_status") or record.get("status") or "")
        status = published_status(probe_status, kind)
        if status is None:
            raise CfError(f"{name}: published status {probe_status!r} of a {kind or 'kindless'} "
                          f"record is not one ingest maps (one of "
                          f"{', '.join(sorted({*PUBLISHED_STATUSES, PUBLISHED_COMPLETE}))}; "
                          f"{PUBLISHED_COMPLETE} only on the kinds "
                          f"{', '.join(sorted(PUBLISHED_COMPLETE_BY_KIND))})",
                          hint="a status ingest cannot map is refused, never guessed")
        statement = statement_flag(kind, record.get("signature")) and status != vocab.PROOF_PROVED
        flags = [vocab.INDEX_FLAG_PUBLISHED_STATUS]
    elif published:
        status, statement, flags = decide(record)
        flags = sorted(set(flags) | {vocab.INDEX_FLAG_PUBLISHED_STATUS})
    else:
        if probe_status not in PROBE_STATUSES:
            raise CfError(f"{name}: probe status {probe_status!r} is not one of "
                          f"{', '.join(sorted(PROBE_STATUSES))}")
        check_consistent(record)
        status, statement, flags = decide(record)
    closure = record.get("nonstandard_axioms") if isinstance(record.get("nonstandard_axioms"),
                                                              list) else []
    return {
        "name": name, "module": module, "path": path,
        "line": record.get("line"), "start_line": record.get("start_line"),
        "end_line": record.get("end_line"), "kind": kind,
        "proof_status": status, "statement": statement, "printable": _printable(record),
        "signature": record.get("signature"), "docstring": record.get("docstring"),
        "axioms": sorted({str(a) for a in closure}
                         | ({name} if kind == "axiom" and name not in vocab.STANDARD_AXIOMS
                            else set())),
        "tags": tags_of(record, path, checkout),
        "deprecated": record.get("deprecated"),
        "private": bool(record.get("private")),
        "probe_proof_status": probe_status or None,
        "flags": sorted(set(flags)),
    }


def _dumps(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _name_of(line: str) -> str:
    return json.loads(line)["name"]


def _spill(buffer: list[tuple[str, str]], work: Path, index: int) -> Path:
    path = work / f"chunk-{index}.jsonl"
    buffer.sort(key=lambda pair: pair[0])
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for _name, line in buffer:
            handle.write(line + "\n")
    return path


def _in_name_order(records: Iterator[dict], work: Path, presorted: bool) -> Iterator[str]:
    """The records' JSON lines in name order: as they come, or through sorted chunks."""
    if presorted:
        for record in records:
            yield _dumps(record)
        return
    chunks: list[Path] = []
    buffer: list[tuple[str, str]] = []
    for record in records:
        buffer.append((record["name"], _dumps(record)))
        if len(buffer) >= CHUNK_RECORDS:
            chunks.append(_spill(buffer, work, len(chunks)))
            buffer = []
    if buffer:
        chunks.append(_spill(buffer, work, len(chunks)))
    files = [open(p, encoding="utf-8") for p in chunks]
    try:
        for _name, line in heapq.merge(*(((_name_of(l), l) for l in f) for f in files)):
            yield line.rstrip("\n")
    finally:
        for f in files:
            f.close()


def _write_resolved(lines: Iterator[str], out: Path, dropped: list[str]) -> int:
    """Write name-ordered lines, one per name: refuse two public records, drop private twins."""
    count = 0
    group: list[str] = []
    group_name: str | None = None

    def flush() -> None:
        nonlocal count
        if not group:
            return
        if len(group) == 1:
            handle.write(group[0] + "\n")
            count += 1
            return
        public = [line for line in group if not json.loads(line).get("private")]
        if len(public) > 1:
            raise CfError(f"two public declarations named {group_name} in one library",
                          hint="the exact table keys on (library, commit, name)")
        dropped.extend([group_name] * (len(group) - len(public)))
        if public:
            handle.write(public[0] + "\n")
            count += 1

    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            name = _name_of(line)
            if name != group_name:
                flush()
                group, group_name = [], name
            group.append(line)
        flush()
    return count


# --------------------------------------------------------------------------
# the two entry points
# --------------------------------------------------------------------------

def _source_manifest(source: Path) -> tuple[dict, str | None]:
    path = (source / "manifest.json") if source.is_dir() else source.with_name("manifest.json")
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CfError(f"{path} is not readable JSON ({exc})") from None
    return (data if isinstance(data, dict) else {}), sha256_file(path)


def _one(values: dict[str, object], what: str, *, required: bool):
    """The one value several places name for one fact; refused when they disagree."""
    named = {where: v for where, v in values.items() if v not in (None, "")}
    distinct = {json.dumps(v, sort_keys=True) for v in named.values()}
    if len(distinct) > 1:
        raise CfError(f"the index's {what} disagree: " +
                      ", ".join(f"{where} says {v!r}" for where, v in named.items()),
                      hint="the index.jsonl and the manifest.json beside it must come from one "
                           "probe run; copy the whole output directory")
    if not named:
        if required:
            raise CfError(f"the index names no {what}",
                          hint="ingest the probe's whole output directory: index.jsonl and its "
                               "manifest.json")
        return None
    return next(iter(named.values()))


def _check_identity(lib: dict, library_id: str, header: dict, manifest: dict, *,
                    published: bool) -> str:
    """The library, commit and toolchain the index names are the pin's; returns the toolchain."""
    library = _one({"the header": None if published else header.get("library"),
                    "manifest.json": manifest.get("library")}, "library", required=True)
    if library != library_id:
        raise CfError(f"the index is for library {library!r}, not {library_id!r}")
    commit = _one({"the header": header.get("commit"), "manifest.json": manifest.get("commit")},
                  "commit", required=True)
    if commit != lib.get("commit"):
        raise CfError(f"the index was made at {str(commit)[:12]}, but the universe pins "
                      f"{library_id} at {str(lib.get('commit'))[:12]}",
                      hint="probe the pinned commit; a universe is frozen")
    # the probe's header carries Lean.versionString, a published header its lean_toolchain;
    # the manifest the elan spelling of lean-toolchain: both are compared by Lean version
    header_toolchain = header.get("lean_toolchain" if published else "toolchain")
    if not header_toolchain and not published:
        raise CfError("the probe header names no toolchain",
                      hint="the probe records Lean.versionString in its header; an index "
                           "without it was not written by this probe")
    toolchain = header_toolchain or manifest.get("toolchain")
    if not toolchain:
        raise CfError("the index names no toolchain, in its header or its manifest")
    for where, value in (("the index header", header_toolchain),
                         ("manifest.json", manifest.get("toolchain"))):
        if value and lean_version(value) != lean_version(lib.get("toolchain")):
            raise CfError(f"{where} says Lean {lean_version(value)}, but {library_id} is pinned "
                          f"to {lib.get('toolchain')}")
    if manifest.get("dirty") is True:
        raise CfError("the probe's manifest says the probed checkout was dirty",
                      hint="probe a clean checkout of the pinned commit")
    if "dirty" in manifest and manifest["dirty"] is None:
        raise CfError("the probe could not tell whether the probed checkout was clean",
                      hint="re-probe where git can read the checkout safely (git 2.31 or later)")
    return str(toolchain)


def _check_whole(index: Path, header: dict, manifest: dict, scan: dict, *,
                 published: bool) -> str:
    """The bytes and the record counts are the ones the probe wrote; returns the index sha."""
    digest = sha256_file(index)
    want = manifest.get("index_sha256")
    if want is None and not published:
        raise CfError("the probe's manifest.json records no index_sha256",
                      hint="probe_merge.py writes it; ingest the whole output directory")
    if want is not None and want != digest:
        raise CfError(f"{index.name} has sha256 {digest[:12]}…, but its manifest records "
                      f"{str(want)[:12]}…",
                      hint="the index changed after the probe wrote it, or the copy was cut "
                           "short; copy it again")
    size = manifest.get("index_bytes")
    if size is not None and size != index.stat().st_size:
        raise CfError(f"{index.name} is {index.stat().st_size} bytes, its manifest says {size}")
    counts = {"the header's declaration_count": header.get("declaration_count"),
              "manifest.json records.declaration": (manifest.get("records") or {}).get(
                  "declaration") if isinstance(manifest.get("records"), dict) else None}
    for where, value in counts.items():
        if value is not None and value != scan["declarations"]:
            raise CfError(f"{where} is {value}, but {index.name} holds {scan['declarations']} "
                          f"declaration records", hint="the index is not whole; copy it again")
    modules = (manifest.get("records") or {}).get("module") \
        if isinstance(manifest.get("records"), dict) else None
    if modules is not None and modules != scan["modules"]:
        raise CfError(f"manifest.json records {modules} modules, {index.name} holds "
                      f"{scan['modules']}")
    if not published and scan["modules"] and not any(scan["paths"].values()):
        raise CfError(f"no module of {index.name} has a source path",
                      hint="the probe never found the sources -- a SRC_DIR that did not reach "
                           "probe_host.sh as SRC_DIRS; re-probe with the fixed driver")
    return digest


def _decls_of(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def _declarations(index: Path, module_paths: dict, *, published: bool,
                  checkout: _Checkout) -> Iterator[dict]:
    for line_no, record in _records(index):
        if line_no == 1 or record.get("record") != "declaration":
            continue
        try:
            yield normalise(record, module_paths, checkout, published=published, line_no=line_no)
        except CfError as exc:
            raise CfError(f"{index.name} line {line_no}: {exc}", hint=exc.hint) from None


def ingest(universe: Universe, library_id: str, source: Path, out_dir: Path, *,
           checkout: Path | None = None, accept_published: str | None = None) -> dict:
    """Ingest one library's index into ``out_dir`` and pin it in ``universe``; the manifest.

    Refused: a library the universe does not list (or excludes); an index whose
    header or manifest names another library, commit or toolchain, or which do
    not agree; an index that is not whole; a probe index on a toolchain whose
    canary has not passed; a published index nobody accepted by name; a
    checkout not at the pin or not clean; and any record ingest cannot decide.
    ``universe`` is changed in memory; the caller saves it.
    """
    lib = universe.library(library_id)
    source = Path(source)
    index = _index_file(source)
    header = read_header(index)
    published = header.get("schema") == PUBLISHED_SCHEMA
    manifest_in, manifest_sha = _source_manifest(source)
    accepted = " ".join(str(accept_published or "").split())
    if published and not accepted:
        raise CfError(f"{index.name} is a published {PUBLISHED_SCHEMA} index, which this "
                      f"probe and its canary never checked",
                      hint="accept it by name with --accept-published '<why it is trusted>' "
                           "(recorded in universe.yaml), or probe the library instead")
    if not published and accepted:
        raise CfError("--accept-published is for a published index; this one is the probe's")
    if not published and not manifest_in:
        raise CfError(f"no manifest.json beside {index.name}",
                      hint="probe_merge.py writes one; ingest the whole output directory")
    toolchain = _check_identity(lib, library_id, header, manifest_in, published=published)
    if not published and not universe.canary_passed(toolchain):
        raise CfError(f"no passed acceptance canary for Lean {lean_version(toolchain)} in "
                      f"universe {universe.id}",
                      hint="build and probe templates/corpus/canary/ on that toolchain, then "
                           "`cf corpus ingest <universe> --canary --from <its index>` "
                           "(docs/proposals/wikipedia-locate.md section 2.4)")
    scan = _scan(index)
    index_sha = _check_whole(index, header, manifest_in, scan, published=published)
    if checkout is not None:
        check_checkout(Path(checkout), str(lib.get("commit")))
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    reader = _Checkout(checkout)
    work = Path(tempfile.mkdtemp(prefix=f".{library_id}-", dir=out_dir.parent))
    dropped: list[str] = []
    try:
        staged = work / "out"
        staged.mkdir()
        decls_path = staged / "decls.jsonl"
        counts = {"proof_status": Counter(), "kind": Counter(), "flags": Counter(),
                  "tags": Counter(), "statement": 0, "unprintable": 0}

        records = _declarations(index, scan["paths"], published=published, checkout=reader)
        total = _write_resolved(_in_name_order(records, work, scan["sorted"]), decls_path,
                                dropped)
        for record in _decls_of(decls_path):
            counts["proof_status"][record["proof_status"]] += 1
            counts["kind"][record["kind"]] += 1
            counts["flags"].update(record["flags"])
            counts["tags"].update(t["tag"] for t in record["tags"])
            counts["statement"] += int(record["statement"])
            counts["unprintable"] += int(not record["printable"])
        digest = sha256_file(decls_path)
        manifest = {
            "schema": INDEX_SCHEMA, "library": library_id, "universe": universe.id,
            "commit": lib.get("commit"), "toolchain": lib.get("toolchain"),
            "lean_version": lean_version(toolchain), "declarations": total,
            "sha256": digest,
            "by_proof_status": dict(sorted(counts["proof_status"].items())),
            "by_kind": dict(sorted(counts["kind"].items())),
            "flagged": dict(sorted(counts["flags"].items())),
            "statement_definitions": counts["statement"],
            "unprintable": counts["unprintable"],
            "private_collisions_dropped": {"count": len(dropped),
                                           "names": sorted(set(dropped))[:_NAMED]},
            "modules": scan["modules"],
            "modules_without_path": sum(1 for p in scan["paths"].values() if not p),
            "tags": dict(sorted(counts["tags"].items())),
            "tags_read_from_checkout": checkout is not None,
            "checkout_files_unreadable": len(reader.unreadable),
            "proof_wanted_scan": "not implemented",
            "source": {"schema": header.get("schema"), "index_sha256": index_sha,
                       "manifest_sha256": manifest_sha,
                       "probe_revision": header.get("probe_revision"),
                       "template_sha256": manifest_in.get("template_sha256")},
            "published": ({"schema": header.get("schema"), "reason": accepted}
                          if published else None),
            "ingested": state_mod.now(),
        }
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)
                                              + "\n", encoding="utf-8")
        if out_dir.exists():
            shutil.rmtree(out_dir)
        os.replace(staged, out_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    universe.set_index(library_id, path=f"index/{library_id}/", sha256=digest,
                       declarations=total,
                       published=({"schema": header.get("schema"), "reason": accepted,
                                   "accepted": manifest["ingested"]} if published else None))
    return manifest


def check_canary(universe: Universe, source: Path) -> dict:
    """Ingest a canary probe index and record whether its toolchain passes.

    The four declarations of templates/corpus/canary/ must each be decided as
    :data:`CANARY` says.  A missing one fails the canary: the probe that cannot
    see a declaration cannot classify it.  ``universe`` is changed in memory.
    """
    source = Path(source)
    index = _index_file(source)
    header = read_header(index)
    if header.get("schema") != PROBE_SCHEMA:
        raise CfError(f"a canary is a probe index ({PROBE_SCHEMA}), not {header.get('schema')!r}")
    toolchain = str(header.get("toolchain") or "")
    if not toolchain:
        raise CfError(f"{index.name}'s header names no toolchain")
    got: dict[str, dict] = {}
    scan = _scan(index)
    for record in _declarations(index, scan["paths"], published=False, checkout=_Checkout(None)):
        if record["name"] in CANARY:
            got[record["name"]] = record
    cases = {}
    for name, want in CANARY.items():
        record = got.get(name)
        result = ({"proof_status": record["proof_status"], "statement": record["statement"]}
                  if record else None)
        cases[name] = {"expected": want, "got": result, "ok": result == want}
    passed = all(case["ok"] for case in cases.values())
    universe.record_canary(toolchain=toolchain, passed=passed, cases=cases,
                           index_sha256=sha256_file(index), checked=state_mod.now())
    return {"toolchain": toolchain, "lean_version": lean_version(toolchain), "passed": passed,
            "cases": cases}
