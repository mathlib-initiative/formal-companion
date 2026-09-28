"""Mechanical ids: anonymous Lean declarations and unnumbered passages.

New code, implementing docs/annotations.md section 5.  The declaration rule was
measured before it was written down: over the full first-parent history of the
two worked companions, a statement-hash id was retired only when the statement
itself changed, while line-based ids (``example@<file>:<line>``, the form the
adapters emit today) churned on unrelated edits above them.

Declarations (section 5.1):

* A **named** declaration's id is its fully qualified name.  Its optional
  ``signature`` is the same cut (:func:`named_signature`), omitted when no
  ``:=`` or ``where`` ends a typed header.
* A **private** declaration's id is ``private:<module>:<declared full name>``:
  Lean's own ``_private.<Module>.0.<Name>`` without the mangling.  The module is
  part of the id because Lean allows the same private name in two modules.
* An ``example`` or an **anonymous** ``instance`` (including ``local`` and
  ``scoped`` ones) gets ``<keyword>:<first 12 hex digits of statement_sha256>``,
  where ``statement_sha256`` is the sha256 of :func:`normalize_statement`
  applied to the source text from the keyword up to the ``:=`` (or the
  top-level ``where``) that ends the header, with every comment and the
  docstring removed.  That normalised text is the declaration's ``signature``.
  String and character literals are opaque: a ``:=`` or a bracket inside one
  never ends or nests the header, and an ``example`` inside one is not a
  declaration.  An untyped header (``example (d : ℕ) := t``) hashes the whole
  command, because the term is what determines the proposition.  When two such
  declarations of one companion normalise to the same text, the second and
  later ones, in (file, line) order, take ``~2``, ``~3`` and so on.  The
  ordinal is positional, so it can rebind when a copy is added or deleted: the
  checker refuses a ``~k`` id unless the document's changelog records the
  assignment with the declaration's file (``ordinal_assigned``), and
  :func:`ordinal_ids` lists the ids that need one.
* A **declared** id overrides the hash.  It is accepted only from a comment
  directly above the declaration whose whole content is the tag
  (``/- `example:<slug>` -/``), or from a docstring line that is exactly
  ``lean-companion id: <id>``.  The grammar is ``cflib.vocab``'s, shared with
  the checker.  A comment that starts like a tag but breaks a rule is reported
  in ``rejected_tag``, never guessed.

Passages (section 5.2): ``passage-<8 hex>`` of the normalised anchor quote, or
``passage-p<printed page>-<8 hex>`` of the normalised title when there is no
quote.

The statement normaliser is whitespace only and frozen, for the same reason
``cflib.items.clean`` is: changing it re-keys every anonymous declaration.  The
hash is syntactic: a binder rename or a notation change retires the id, and
``variable`` / ``open`` / namespace context is not seen.
"""
from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cflib import vocab                                              # noqa: E402

HASH_ID = re.compile(r"^" + vocab.HASH_ID_PATTERN + r"$")
DECLARED_ID = re.compile(r"^" + vocab.DECLARED_ID_PATTERN + r"$")

_MODIFIERS = ("private", "protected", "noncomputable", "unsafe", "partial", "nonrec")
#: One-line command prefixes before the keyword: ``open X in``, ``set_option x v in``,
#: ``variable (n : ℕ) in``, ``include h in``, ``omit [Foo] in`` and ``#guard_msgs in``
#: (with or without its ``(...)`` options).
_ONE_PREFIX = (r"(?:(?:open|set_option|variable|include|omit)[ \t][^\n]*?[ \t]in"
               r"|\#guard_msgs(?:[ \t]*\([^()\n]*\))?[ \t]+in)")
_PREFIX = r"(?:" + _ONE_PREFIX + r"[ \t]+)*"
_DECL_RE = re.compile(
    r"^[ \t]*" + _PREFIX + r"(?:@\[[^\]]*\][ \t\n]*)*"
    r"(?P<mods>(?:(?:" + "|".join(_MODIFIERS) + r")[ \t]+)*)"
    r"(?:(?P<attrkind>local|scoped)[ \t]+(?=instance))?"
    r"(?P<kw>example|instance)(?![\w'!?.])(?P<rest>.*)$", re.M)
_NAME_RE = re.compile(r"[ \t]+(?P<name>[^\s:({\[⦃]+)")
_PRIORITY_RE = re.compile(r"\s*\(priority[^)]*\)\s*(?P<name>[^\s:({\[⦃]+)?")
#: What may sit between a tag comment and its declaration besides whitespace.
_GAP_NOISE = re.compile(r"@\[[^\]]*\]|" + _ONE_PREFIX + r"\b")
_TAG_LIKE = re.compile(r"^`(?:example|instance):")
_ID_LINE = re.compile(r"^lean-companion id:")
_OPEN = "([{⟨⦃⟦"
_CLOSE = ")]}⟩⦄⟧"
_IDENT = "_'!?"
_TYPE_BINDERS = ("let", "have", "letI", "haveI", "let_fun")


def normalize_statement(text: str) -> str:
    """Collapse every run of whitespace to one space and strip the ends.  Frozen."""
    return re.sub(r"\s+", " ", text).strip()


def statement_sha256(statement: str) -> str:
    """sha256 of the normalised statement, as 64 lowercase hex digits."""
    return hashlib.sha256(normalize_statement(statement).encode("utf-8")).hexdigest()


def anonymous_id(keyword: str, digest: str, ordinal: int = 1) -> str:
    """``example:<12 hex>`` or ``instance:<12 hex>``, with ``~k`` for k >= 2."""
    if keyword not in vocab.ANONYMOUS_KEYWORDS:
        raise ValueError(f"not an anonymous keyword: {keyword!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("digest must be 64 lowercase hex digits")
    if ordinal < 1:
        raise ValueError("ordinal starts at 1")
    return f"{keyword}:{digest[:12]}" + (f"~{ordinal}" if ordinal > 1 else "")


def private_id(module: str, full_name: str) -> str:
    """``private:<module>:<declared full name>``."""
    if not module or not full_name:
        raise ValueError("a private id needs a module and a name")
    return f"private:{module}:{full_name}"


# ---------------------------------------------------------------------------
# passages and numbered items (section 5.2)
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """NFKC, then every run of whitespace to one space, ends stripped.  Frozen.

    The passage-id normaliser.  NFKC comes first because it can turn a
    character into whitespace (U+00A0) and it folds ligatures and the micro
    sign, which differ between PDF text extractors.  Case is kept.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _h8(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:8]


def passage_quote_id(quote: str) -> str:
    """``passage-<8 hex>``: the id of a passage whose anchor quote is ``quote``."""
    if not normalize_text(quote):
        raise ValueError("an empty quote cannot identify a passage")
    return f"passage-{_h8(quote)}"


def passage_page_id(printed_page: int, title: str) -> str:
    """``passage-p<printed page>-<8 hex of the title>``, for a passage with no quote."""
    if not isinstance(printed_page, int) or isinstance(printed_page, bool) or printed_page < 1:
        raise ValueError("the printed page is a positive integer")
    if not normalize_text(title):
        raise ValueError("an empty title cannot identify a passage")
    return f"passage-p{printed_page}-{_h8(title)}"


def item_label_id(kind: str, number: str, sub: str | None = None) -> str:
    """``<kind>-<number>[-<sub>]``, the kind lower-cased with each run of other characters as ``-``."""
    slug = re.sub(r"[^a-z0-9]+", "-", kind.lower()).strip("-")
    if not slug or not number:
        raise ValueError("a label id needs a kind and a number")
    return f"{slug}-{number}" + (f"-{sub}" if sub else "")


# ---------------------------------------------------------------------------
# scanning Lean
# ---------------------------------------------------------------------------

def _literal_end(text: str, i: int) -> int | None:
    """The end of a string or character literal starting at ``i``, or None.

    Ordinary (``"..."``, also after ``s!`` or ``m!``) and raw (``r"..."``,
    ``r#"..."#``) strings, and character literals (``'a'``, ``'\\n'``,
    ``'\\x41'``, ``'\\u{3bb}'``).  A ``'`` after an identifier character is a
    prime (``f'``), not a literal.
    """
    n = len(text)
    c = text[i]
    before = text[i - 1] if i > 0 else " "
    if c == '"':
        j = i + 1
        while j < n and text[j] != '"':
            j += 2 if text[j] == "\\" else 1
        return min(j + 1, n)
    if c == "r" and not (before.isalnum() or before in _IDENT or before == "."):
        m = re.match(r'r(#*)"', text[i:i + 64])
        if m:
            close = '"' + m.group(1)
            j = text.find(close, i + len(m.group(0)))
            return n if j < 0 else j + len(close)
        return None
    if c == "'" and not (before.isalnum() or before in "_'."):
        if i + 2 < n and text[i + 1] != "\\" and text[i + 1] != "'" and text[i + 2] == "'":
            return i + 3
        m = re.match(r"'\\(?:[\\'\"ntr0]|x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]{1,6}\})'", text[i:i + 12])
        if m:
            return i + len(m.group(0))
    return None


def _scan_lean(text: str) -> tuple[str, list[tuple[int, int, str]], list[tuple[int, int]]]:
    """Comment-blanked code, the comment spans and the literal spans of ``text``."""
    out = list(text)
    spans: list[tuple[int, int, str]] = []
    literals: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("/-", i):
            kind = "doc" if text.startswith("/--", i) else "moddoc" if text.startswith("/-!", i) else "block"
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/-", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("-/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            spans.append((i, j, kind))
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            spans.append((i, j, "line"))
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        end = _literal_end(text, i)
        if end is not None:
            literals.append((i, end))
            i = end
            continue
        i += 1
    return "".join(out), spans, literals


def strip_comments(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Blank out comments, keeping offsets and newlines.

    Returns the blanked code and the comment spans ``(start, end, kind)``,
    ``kind`` one of ``doc`` (``/--``), ``moddoc`` (``/-!``), ``block`` or
    ``line``.  Block comments nest, as in Lean; string and character literals
    are skipped so a ``--`` inside one is not a comment.
    """
    code, spans, _literals = _scan_lean(text)
    return code, spans


def blank_literals(code: str) -> str:
    """``code`` with the inside of every string and character literal blanked.

    Offsets and newlines are kept.  This is the copy the scanner reads to find
    declarations and cut headers, so a ``:=``, a bracket or a keyword inside a
    literal never counts; the signature itself is still cut from ``code``,
    literals included.
    """
    out = list(code)
    for a, b in _scan_lean(code)[2]:
        for k in range(a + 1, b - 1):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


def token_at(code: str, i: int, word: str) -> bool:
    """Is the identifier ``word`` at offset ``i`` of ``code``, as a whole token?"""
    if not code.startswith(word, i):
        return False
    before = code[i - 1] if i > 0 else " "
    after = code[i + len(word)] if i + len(word) < len(code) else " "
    return (not (before.isalnum() or before in _IDENT or before == ".")
            and not (after.isalnum() or after in _IDENT))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _command_end(code: str, start: int) -> int:
    """The end of the command whose keyword is at ``start``.

    It ends before the next non-blank line indented no deeper than the
    keyword's own line, so an indented declaration inside a block stops at its
    sibling rather than at the next column-0 line.  For a keyword in column 0
    this is the next line that starts with code in column 0.
    """
    line_start = code.rfind("\n", 0, start) + 1
    depth = _indent(code[line_start:start])
    i = code.find("\n", start)
    while i != -1:
        j = i + 1
        k = code.find("\n", j)
        line = code[j:] if k == -1 else code[j:k]
        if line.strip() and _indent(line) <= depth:
            return i
        i = k
    return len(code)


def statement_span(code: str, start: int, keyword: str) -> tuple[int, bool]:
    """Where the statement that starts at ``start`` ends, and whether it is typed.

    ``code`` is comment- and literal-blanked (:func:`blank_literals`).  The
    statement ends at the first ``:=`` or ``where`` at bracket depth 0 after the
    header's type colon, skipping one ``:=`` for each ``let`` / ``have`` binder
    inside the type.  An instance also ends at a match-alternative line.  With
    no type colon the whole command is the statement (``typed`` is False).
    """
    limit = _command_end(code, start)
    depth, i, typed, pending = 0, start + len(keyword), False, 0
    while i < limit:
        c = code[i]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0:
            if code.startswith(":=", i):
                if typed and pending:
                    pending -= 1
                    i += 2
                    continue
                return (i, True) if typed else (limit, False)
            if c == ":" and not typed:
                typed = True
            elif typed and any(token_at(code, i, w) for w in _TYPE_BINDERS):
                pending += 1
            elif typed and (token_at(code, i, "where")
                            or (keyword == "instance" and code.startswith("\n  | ", i))):
                return i, True
        i += 1
    return limit, typed


#: Keywords whose body follows the header without a ``:=``: no signature.
_NO_SIGNATURE = ("structure", "class", "inductive")


def named_signature(code: str, scan: str, start: int, keyword: str) -> str | None:
    """The section 5.1 cut for a named declaration whose keyword is at ``start``.

    ``code`` is comment-blanked and ``scan`` is ``blank_literals(code)``.  The
    same cut as an anonymous declaration's, returned only when a ``:=`` or a
    ``where`` (or an instance's match alternative) ends a typed header; None
    for an untyped header, a definition by pattern matching, an ``axiom`` or
    ``opaque`` without a value, and a ``structure``, ``class`` or ``inductive``.
    """
    if keyword in _NO_SIGNATURE:
        return None
    end, typed = statement_span(scan, start, keyword)
    if not typed or end >= len(scan):
        return None
    if not (scan.startswith(":=", end) or token_at(scan, end, "where") or scan.startswith("\n  | ", end)):
        return None
    if re.search(r"\n[ \t]*\|[ \t]", scan[start:end]):
        return None          # a definition by pattern matching whose ``where`` is indented
    return normalize_statement(code[start:end])


@dataclass(frozen=True)
class AnonymousDecl:
    """One ``example`` or anonymous ``instance`` found in a file.

    ``signature`` is the normalised statement text ``statement_sha256`` hashes.
    ``rejected_tag`` explains a comment above the declaration that looked like
    a declared-id tag but was refused (``None`` when there was none).
    ``comment_tags`` lists every backticked ``<keyword>:<slug>`` tag that opens
    a comment directly above, declared or refused alike: a companion may use
    that shape as a plain comment tag (measure_theory's ledger ids), and an
    exporter joins on it as a legacy id even when it declares nothing.
    """
    file: str
    line: int
    keyword: str
    statement_sha256: str
    typed: bool
    declared_id: str | None
    signature: str = ""
    rejected_tag: str | None = None
    comment_tags: tuple[str, ...] = ()


def _comments_above(text: str, spans, decl_start: int) -> list[tuple[int, int, str]]:
    """The run of comments directly above ``decl_start``, nearest first.

    Only whitespace, attributes and the one-line ``... in`` prefixes (``open``,
    ``set_option``, ``variable``, ``include``, ``omit``, ``#guard_msgs``) may
    separate them from the declaration and from each other.
    """
    run: list[tuple[int, int, str]] = []
    edge = decl_start
    for a, b, kind in reversed(spans):
        if b > edge:
            continue
        if _GAP_NOISE.sub("", text[b:edge]).strip():
            break
        run.append((a, b, kind))
        edge = a
    return run


def _comment_body(text: str, a: int, b: int, kind: str) -> str:
    body = text[a:b]
    if kind == "line":
        return body[2:].strip()
    opener = 3 if kind in ("doc", "moddoc") else 2
    return body[opener:-2 if body.endswith("-/") else len(body)].strip()


def declared_tag(text: str, spans, decl_start: int, keyword: str) -> tuple[str | None, str | None]:
    """``(declared id, rejection)`` from the comments directly above a declaration."""
    found: list[str] = []
    rejected: list[str] = []
    for a, b, kind in _comments_above(text, spans, decl_start):
        body = _comment_body(text, a, b, kind)
        if _TAG_LIKE.match(body):
            m = re.fullmatch(r"`([^`\s]+)`", body)
            if not m:
                rejected.append(f"{body.splitlines()[0][:60]!r}: a tag comment holds the tag and nothing else")
            elif not DECLARED_ID.match(m.group(1)):
                rejected.append(f"{m.group(1)!r} is not a declared id (<keyword>:<slug>)")
            else:
                found.append(m.group(1))
        if kind == "doc":
            for line in body.splitlines():
                line = line.strip()
                if not _ID_LINE.match(line):
                    continue
                value = line[len("lean-companion id:"):].strip()
                if DECLARED_ID.match(value):
                    found.append(value)
                else:
                    rejected.append(f"{value!r} is not a declared id (<keyword>:<slug>)")
    for tag in found:
        if not tag.startswith(keyword + ":"):
            rejected.append(f"{tag!r} is on a {keyword}")
    found = [t for t in found if t.startswith(keyword + ":")]
    if len(set(found)) > 1:
        rejected.append(f"two different tags: {sorted(set(found))}")
        found = []
    return (found[0] if found else None), ("; ".join(rejected) or None)


_TAG_OPENING = re.compile(r"^`((?:example|instance):[^`\s]+)`")


def comment_tags(text: str, spans, decl_start: int) -> tuple[str, ...]:
    """The ``<keyword>:<slug>`` tags that OPEN the comments directly above a declaration.

    The same comments :func:`declared_tag` reads, but every tag-shaped opening
    counts, whether or not the comment declares an id: a tag that is the first
    line of a longer comment is refused as a declared id (section 5.1) and is
    still the name a companion's own tables know the declaration by.
    """
    found: list[str] = []
    for a, b, kind in _comments_above(text, spans, decl_start):
        m = _TAG_OPENING.match(_comment_body(text, a, b, kind))
        if m:
            found.append(m.group(1))
    return tuple(found)


def scan_file(text: str, path: str) -> list[AnonymousDecl]:
    """Every ``example`` and anonymous ``instance`` in one Lean file."""
    code, spans, literals = _scan_lean(text)
    scan = list(code)
    for a, b in literals:
        for k in range(a + 1, b - 1):
            if scan[k] != "\n":
                scan[k] = " "
    scan = "".join(scan)
    found: list[AnonymousDecl] = []
    for m in _DECL_RE.finditer(scan):
        kw, rest = m.group("kw"), m.group("rest")
        if kw == "instance":
            if rest.lstrip().startswith("(priority"):
                pr = _PRIORITY_RE.match(rest)
                name = pr.group("name") if pr and pr.group("name") not in (None, ":") else None
            else:
                nm = _NAME_RE.match(rest)
                cand = nm.group("name") if nm else None
                name = cand if cand and cand not in (":", "where") and cand[0] not in "([" else None
            if name is not None:
                continue                                  # a named instance
        start = m.start("kw")
        end, typed = statement_span(scan, start, kw)
        signature = normalize_statement(code[start:end])
        declared, rejected = declared_tag(text, spans, m.start(), kw)
        found.append(AnonymousDecl(
            file=path, line=code.count("\n", 0, start) + 1, keyword=kw,
            statement_sha256=statement_sha256(signature), typed=typed,
            declared_id=declared, signature=signature, rejected_tag=rejected,
            comment_tags=comment_tags(text, spans, m.start())))
    return found


def assign_ids(decls: list[AnonymousDecl]) -> dict[AnonymousDecl, tuple[str, str]]:
    """``{decl: (id, id_source)}`` for one companion's anonymous declarations.

    Ordinals are assigned over the whole companion in (file, line) order, among
    the statement-hash ids only: a declared id takes no ordinal and shifts none.
    """
    seen: dict[tuple[str, str], int] = {}
    out: dict[AnonymousDecl, tuple[str, str]] = {}
    for d in sorted(decls, key=lambda d: (d.file, d.line)):
        if d.declared_id:
            out[d] = (d.declared_id, "declared")
            continue
        key = (d.keyword, d.statement_sha256)
        seen[key] = seen.get(key, 0) + 1
        out[d] = (anonymous_id(d.keyword, d.statement_sha256, seen[key]), "statement_hash")
    return out


def ordinal_ids(assigned: dict[AnonymousDecl, tuple[str, str]]) -> list[tuple[str, AnonymousDecl]]:
    """The ``~k`` ids in ``assigned``, each with its declaration, sorted by id.

    Each one needs an ``ordinal_assigned`` changelog entry (or a declared id on
    the declaration) before a document carrying it validates.
    """
    out = [(rid, d) for d, (rid, source) in assigned.items()
           if source == "statement_hash" and "~" in rid]
    return sorted(out, key=lambda pair: pair[0])
