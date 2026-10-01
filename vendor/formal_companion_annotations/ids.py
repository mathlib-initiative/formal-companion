"""Mechanical ids: anonymous Lean declarations and unnumbered passages.

The reference implementation of ``docs/id-algorithm.md``; the vectors under
``conformance/ids/`` pin its output.  Where this module and that document
disagree, one of them has a bug.

Declarations (docs/annotations.md sections 5.3 and 7.1, docs/id-algorithm.md):

* A **named** declaration's id is its fully qualified name.  Its optional
  ``signature`` is the same cut as an anonymous one's (:func:`signature_at`).
* A **private** declaration's id is ``private:<module>:<declared full name>``.
* An ``example`` or an **anonymous** ``instance`` gets
  ``<keyword>:<first 12 hex digits of statement_sha256>``.  Two texts belong to
  it: the *display form* (its ``signature``: the header from the keyword to
  the ``:=``, top-level ``where`` or match-alternative line that ends it,
  comments replaced by a space, whitespace collapsed) and the *hash form*
  (:func:`hash_form`: the display form tokenised and re-joined so that the
  placement of whitespace, line breaks and comments between tokens, and the
  spellings ``fun``/``λ`` and ``=>``/``↦``, do not matter).
  ``statement_sha256`` is the sha256 of the hash form.  An untyped header
  (``example (d : ℕ) := t``) hashes the whole command.  Duplicates within one
  companion take ``~2``, ``~3`` ... in path order (:func:`assign_ids`).
* A **declared** id overrides the hash: a comment directly above whose whole
  content is the tag (``/- `example:<slug>` -/``) or a docstring line
  ``formal-companion id: <id>``.  A tag-like comment that breaks a rule is
  reported in ``rejected_tag``, never guessed.

Passages (docs/annotations.md section 7.2, docs/id-algorithm.md section 13):
``passage-<8 hex>`` of the normalised anchor quote, or
``passage-p<printed page>-<8 hex>`` of the normalised title.

Every rule here is frozen with the format: changing one re-keys ids.
"""
from __future__ import annotations

import bisect
import hashlib
import re
import unicodedata
from dataclasses import dataclass

from . import vocab

#: The stable public API of this module: exactly these names.  Anything else,
#: underscored or not, is internal and may change in any release.
__all__ = [
    "DECLARED_ID", "HASH_ID", "AnonymousDecl", "anonymous_id", "assign_ids", "hash_form",
    "item_label_id", "normalize_statement", "normalize_text", "ordinal_ids", "passage_page_id",
    "passage_quote_id", "private_id", "scan_file", "signature_at", "statement_sha256",
]

#: The anonymous-id grammars (vocab's), anchored at both ends: use ``fullmatch``
#: (``match`` gives the same answer, since ``\A``/``\Z`` are string ends).
HASH_ID = re.compile(r"\A(?:" + vocab.HASH_ID_PATTERN + r")\Z")
DECLARED_ID = re.compile(r"\A(?:" + vocab.DECLARED_ID_PATTERN + r")\Z")

# ---------------------------------------------------------------------------
# character sets (docs/id-algorithm.md, section 1)
# ---------------------------------------------------------------------------

#: Whitespace, everywhere in the declaration rules: exactly these four.
_WS = " \t\r\n"
#: The word characters (a Lean identifier character): ASCII letters and digits,
#: ``_``, ``'``, ``!``, ``?``, ``.``, Greek except λ Π Σ, Greek Extended,
#: letter-like symbols, mathematical alphanumerics, and the subscripts.
_WORD_CLASS = (r"A-Za-z0-9_'!?."
               r"Ͱ-ΟΡ-΢Τ-κμ-Ͽ"
               r"ἀ-῾℀-⅏\U0001d49c-\U0001d59f"
               r"₀-₉ₐ-ₜᵢ-ᵪⱼ")
_WORD_RE = re.compile(rf"[{_WORD_CLASS}]+")
_IS_WORD = re.compile(rf"[{_WORD_CLASS}]").fullmatch
#: The 25 code points with the Unicode ``White_Space`` property (passage ids).
_UNICODE_WHITE_SPACE = ("\u0009\u000a\u000b\u000c\u000d \u0085  "
                        "           "
                        "    　")
_WHITE_SPACE_RUN = re.compile("[" + _UNICODE_WHITE_SPACE + "]+")

_OPEN = "([{⟨⦃⟦"
_CLOSE = ")]}⟩⦄⟧"
#: Binders inside a type whose own ``:=`` does not end the header.
_TYPE_BINDERS = frozenset({"let", "have", "letI", "haveI", "let_fun"})
#: Declaration modifiers (visibility, computability, recursion, meta, attribute kinds).
_MODIFIERS = frozenset({"private", "protected", "public", "noncomputable", "unsafe", "partial",
                        "nonrec", "meta", "local", "scoped"})
#: Commands that prefix another command with ``... in`` on one line.
_PREFIX_WORDS = frozenset({"open", "set_option", "variable", "include", "omit", "attribute",
                           "unseal", "seal", "universe", "suppress_compilation",
                           "unsuppress_compilation", "count_heartbeats", "#guard_msgs"})
#: Keywords that start a command (a token starting with ``#`` and an ASCII
#: letter always does).  With the modifiers and ``@[``, they define the
#: "command-start lines" that bound a header.
_COMMAND_WORDS = frozenset({
    "theorem", "lemma", "def", "abbrev", "instance", "example", "structure", "class", "inductive",
    "axiom", "opaque", "irreducible_def", "alias", "namespace", "section", "end", "open", "export",
    "universe", "variable", "set_option", "attribute", "include", "omit", "unseal", "seal",
    "mutual", "deriving", "import", "module", "macro", "macro_rules", "syntax", "notation",
    "notation3", "infix", "infixl", "infixr", "prefix", "postfix", "elab", "elab_rules",
    "declare_syntax_cat", "binder_predicate", "unif_hint", "initialize", "builtin_initialize",
    "simproc", "simproc_decl", "dsimproc", "dsimproc_decl", "builtin_simproc", "builtin_dsimproc",
    "register_option", "register_builtin_option", "register_simp_attr", "register_label_attr",
    "register_hint", "register_linter_set", "register_grind_attr", "grind_pattern", "run_cmd",
    "run_meta", "run_elab", "declare_config_elab", "declare_command_config_elab",
    "declare_core_config_elab", "add_decl_doc", "recommended_spelling", "deprecated_module",
    "deprecated_syntax", "tactic_extension", "suppress_compilation", "unsuppress_compilation",
    "count_heartbeats", "library_note", "assert_not_exists", "assert_not_imported",
    "assert_exists", "initialize_simps_projections", "proof_wanted", "extend_docs", "extend_doc",
    "mk_iff_of_inductive_prop", "insert_to_additive_translation", "declare_aesop_rule_sets",
    "add_aesop_rules", "erase_aesop_rules", "lrat_proof", "recall", "whatsnew",
}) | _MODIFIERS | _PREFIX_WORDS
#: The first token of a line, if the line can start a command.
_HEAD_TOKEN = re.compile(rf"@\[|#[A-Za-z][{_WORD_CLASS}]*|[{_WORD_CLASS}]+")
_COMMENT_KINDS = frozenset({"doc", "moddoc", "block", "line"})
#: Commands whose own doc-comment-shaped body follows them (never a docstring).
_NOTE_COMMANDS = frozenset({"library_note", "#adaptation_note"})
_TAG_LINE = "formal-companion id:"


# ---------------------------------------------------------------------------
# id forms
# ---------------------------------------------------------------------------

def _utf8(text: str) -> bytes:
    """``text`` as UTF-8.  A lone surrogate is refused: no UTF-8 file holds one (section 2),
    so such text is outside the algorithm (it comes from decoding with ``surrogateescape``)."""
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("the text is not valid Unicode: it holds a lone surrogate") from None


def statement_sha256(signature: str) -> str:
    """sha256 of the hash form of ``signature``, as 64 lowercase hex digits.

    Raises ``ValueError`` when ``signature`` holds a lone surrogate.
    """
    return hashlib.sha256(_utf8(hash_form(signature))).hexdigest()


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
# passages and numbered items (docs/annotations.md section 7.2; docs/id-algorithm.md, section 13)
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """NFKC, then each run of ``White_Space`` code points to one space, ends stripped.

    The passage-id normaliser.  NFKC comes first because it can turn a
    character into whitespace (U+00A0) and it folds ligatures and the micro
    sign, which differ between PDF text extractors.  Case is kept.  Whitespace
    is the 25 code points with the Unicode ``White_Space`` property, written
    out in ``_UNICODE_WHITE_SPACE``: not Python's ``\\s``, which also matches
    U+001C..U+001F.
    """
    return _WHITE_SPACE_RUN.sub(" ", unicodedata.normalize("NFKC", text)).strip(_UNICODE_WHITE_SPACE)


def _h8(text: str) -> str:
    return hashlib.sha256(_utf8(normalize_text(text))).hexdigest()[:8]


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
# lexing Lean (docs/id-algorithm.md, sections 3 and 4)
# ---------------------------------------------------------------------------

_LEX = re.compile(r"/-|--|\"|'|«")
_HOLE = re.compile(r"/-|--|\"|'|«|[{}]")
_COMMENT_DELIM = re.compile(r"/-|-/")
_CHAR_ESCAPE = re.compile(r"'\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)'", re.S)
_NON_NEWLINE = re.compile(r"[^\n]")
_NEWLINE = re.compile(r"\n")
_RAW_START = re.compile(r'r#*"')


def _word_before(text: str, i: int) -> bool:
    """Is the code point before offset ``i`` a word character?"""
    return i > 0 and _IS_WORD(text[i - 1]) is not None


def _comment_end(text: str, i: int) -> tuple[int, str]:
    """The end of the block comment opening at ``i`` (``/-``) and its kind."""
    if text.startswith("/--", i):
        kind, k = "doc", i + 3
    elif text.startswith("/-!", i):
        kind, k = "moddoc", i + 3
    else:
        kind, k = "block", i + 2
    depth = 1
    while depth:
        m = _COMMENT_DELIM.search(text, k)
        if m is None:
            return len(text), kind
        depth += 1 if m.group() == "/-" else -1
        k = m.end()
    return k, kind


def _char_end(text: str, i: int) -> int | None:
    """The end of the character literal at ``i`` (a ``'``), or None when it is a prime or a symbol."""
    if _word_before(text, i):
        return None
    n = len(text)
    if i + 2 < n and text[i + 1] not in "\\'" and text[i + 2] == "'":
        return i + 3
    m = _CHAR_ESCAPE.match(text, i)
    return m.end() if m else None


def _raw_string_span(text: str, i: int) -> tuple[int, int] | None:
    """The span of the raw string ``r#..."..."#...`` whose opening ``"`` is at ``i``, or None."""
    j = i
    while j > 0 and text[j - 1] == "#":
        j -= 1
    if j > 0 and text[j - 1] == "r" and not _word_before(text, j - 1):
        close = '"' + "#" * (i - j)
        k = text.find(close, i + 1)
        return j - 1, (len(text) if k < 0 else k + len(close))
    return None


def _interpolated_at(text: str, i: int) -> bool:
    """Does the string whose ``"`` is at ``i`` open with ``s!``, ``m!`` or ``f!``?"""
    return text[i - 2:i] in ("s!", "m!", "f!") and not _word_before(text, i - 2)


def _string_at(text: str, i: int) -> tuple[int, int]:
    """The span of the string literal whose ``"`` is at ``i``: plain, raw or interpolated.

    A hole ``{...}`` of an interpolated string may hold a string with holes of
    its own (section 3.3).  The open constructs are kept on an explicit stack,
    so the depth of that nesting never limits the lexer.
    """
    raw = _raw_string_span(text, i)
    if raw is not None:
        return raw
    n = len(text)
    # One frame per open construct, innermost last: ["string", interpolated?]
    # or ["hole", brace depth].
    frames: list[list] = [["string", _interpolated_at(text, i)]]
    k = i + 1
    while k < n:
        kind, state = frames[-1]
        if kind == "string":
            c = text[k]
            if c == '"':
                frames.pop()
                k += 1
                if not frames:
                    return i, k
            elif c == "\\":
                k += 2
            elif state and c == "{":
                frames.append(["hole", 1])
                k += 1
            else:
                k += 1
            continue
        m = _HOLE.search(text, k)                   # inside a hole: braces balance, literals and comments skip
        if m is None:
            break
        k = m.start()
        c = text[k]
        if c == "{":
            frames[-1][1] = state + 1
            k += 1
        elif c == "}":
            if state == 1:
                frames.pop()
            else:
                frames[-1][1] = state - 1
            k += 1
        elif c == '"':
            raw = _raw_string_span(text, k)
            if raw is None:
                frames.append(["string", _interpolated_at(text, k)])
                k += 1
            else:
                k = raw[1]
        else:
            end = _skip_literal_or_comment(text, k)
            k = k + 1 if end is None else end
    return i, n


def _skip_literal_or_comment(text: str, i: int) -> int | None:
    """The end of the comment, char literal or ``«»`` starting at ``i``, or None.

    A string is not handled here: :func:`_string_at` reads one, holes included.
    """
    c = text[i]
    if c == "/" and text.startswith("/-", i):
        return _comment_end(text, i)[0]
    if c == "-" and text.startswith("--", i):
        k = text.find("\n", i)
        return len(text) if k < 0 else k
    if c == "'":
        return _char_end(text, i)
    if c == "«":
        k = text.find("»", i + 1)
        return None if k < 0 else k + 1
    return None


def _lex(text: str) -> list[tuple[int, int, str]]:
    """The comment and literal spans of ``text``, in order: ``(start, end, kind)``.

    Kinds: ``doc`` (``/--``), ``moddoc`` (``/-!``), ``block``, ``line``
    (comments); ``string``, ``char``, ``guil`` (``«…»``) (literals).
    """
    spans: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n:
        m = _LEX.search(text, i)
        if m is None:
            break
        j = m.start()
        c = text[j]
        if c == "/":
            end, kind = _comment_end(text, j)
            spans.append((j, end, kind))
            i = end
        elif c == "-":
            end = text.find("\n", j)
            end = n if end < 0 else end
            spans.append((j, end, "line"))
            i = end
        elif c == '"':
            start, end = _string_at(text, j)
            spans.append((start, end, "string"))
            i = end
        elif c == "'":
            end = _char_end(text, j)
            if end is None:
                i = j + 1
            else:
                spans.append((j, end, "char"))
                i = end
        else:
            k = text.find("»", j + 1)
            if k < 0:
                i = j + 1
            else:
                spans.append((j, k + 1, "guil"))
                i = k + 1
    return spans


def _code_view(text: str, spans: list[tuple[int, int, str]]) -> str:
    """``text`` with comments blanked and literals made opaque, offsets and newlines kept.

    Every comment code point other than a newline becomes a space; every
    string or char literal code point becomes ``"``; the inside of a ``«…»``
    becomes spaces.  The scanner reads this copy to find declarations and to
    cut headers; the signature itself is cut from ``text``.
    """
    parts: list[str] = []
    last = 0
    for a, b, kind in spans:
        parts.append(text[last:a])
        seg = text[a:b]
        if kind in _COMMENT_KINDS:
            parts.append(_NON_NEWLINE.sub(" ", seg))
        elif kind == "guil":
            parts.append("«" + _NON_NEWLINE.sub(" ", seg[1:-1]) + "»")
        else:
            parts.append(_NON_NEWLINE.sub('"', seg))
        last = b
    parts.append(text[last:])
    return "".join(parts)


# ---------------------------------------------------------------------------
# display form and hash form (docs/id-algorithm.md, sections 8 and 9)
# ---------------------------------------------------------------------------

def _tokens(text: str) -> list[tuple[str, bool]]:
    """The tokens of ``text``: ``(token, wordlike)``, comments and whitespace dropped.

    A word token is a maximal run of word characters and ``«…»`` groups; a
    literal token is a whole string or char literal; every other non-whitespace
    code point is a symbol token on its own.  Word and literal tokens are
    wordlike.  The alias table is applied: ``↦`` gives ``=>``, ``λ`` gives the
    word ``fun``.
    """
    out: list[tuple[str, bool]] = []
    i, n = 0, len(text)
    word: list[str] = []

    def flush() -> None:
        if word:
            out.append(("".join(word), True))
            word.clear()

    while i < n:
        c = text[i]
        if c == "'":
            end = _char_end(text, i)
            if end is not None:
                flush()
                out.append((text[i:end], True))
                i = end
                continue
        if c == "r" and not _word_before(text, i) and _RAW_START.match(text, i):
            _start, end = _string_at(text, text.index('"', i))
            flush()
            out.append((text[i:end], True))
            i = end
            continue
        if _IS_WORD(c):
            m = _WORD_RE.match(text, i)
            word.append(m.group())
            i = m.end()
            continue
        if c == "«":
            k = text.find("»", i + 1)
            if k >= 0:
                word.append(text[i:k + 1])
                i = k + 1
                continue
        flush()
        if c in _WS:
            i += 1
        elif c == '"':
            out.append((text[i:_string_at(text, i)[1]], True))
            i = len(out[-1][0]) + i
        elif c == "/" and text.startswith("/-", i):
            i = _comment_end(text, i)[0]
        elif c == "-" and text.startswith("--", i):
            k = text.find("\n", i)
            i = n if k < 0 else k
        elif c == "↦":
            out.append(("=>", False))
            i += 1
        elif c == "λ":
            out.append(("fun", True))
            i += 1
        else:
            out.append((c, False))
            i += 1
    flush()
    return out



def hash_form(signature: str) -> str:
    """``H``: the tokens of ``signature`` joined, a space only between two wordlike tokens.

    Defined on a display-form signature; any comment in the input counts as
    whitespace, so for text whose literals are closed
    ``hash_form(text) == hash_form(normalize_statement(text))``
    (docs/id-algorithm.md section 9).
    """
    parts: list[str] = []
    prev_wordlike = False
    for token, wordlike in _tokens(signature):
        if wordlike and prev_wordlike:
            parts.append(" ")
        parts.append(token)
        prev_wordlike = wordlike
    return "".join(parts)


def normalize_statement(text: str) -> str:
    """The display form: comments replaced by one space, each run of whitespace one space, ends stripped.

    Whitespace is U+0009, U+000A, U+000D, U+0020.  String, char and ``«…»``
    literals are kept verbatim, inner whitespace included.  Idempotent; the
    checker verifies a ``signature`` with ``normalize_statement(s) == s``.
    """
    parts: list[str] = []
    code: list[str] = []            # the code between two literals, comments already spaces
    last = 0
    for a, b, kind in _lex(text):
        code.append(text[last:a])
        if kind in _COMMENT_KINDS:
            code.append(" ")
        else:
            parts.append(_collapse("".join(code)))
            parts.append(text[a:b])
            code = []
        last = b
    code.append(text[last:])
    parts.append(_collapse("".join(code)))
    return "".join(parts).strip(_WS)


_WS_RUN = re.compile(f"[{_WS}]+")


def _collapse(text: str) -> str:
    return _WS_RUN.sub(" ", text)


# ---------------------------------------------------------------------------
# finding declarations (docs/id-algorithm.md, sections 5 to 7)
# ---------------------------------------------------------------------------

class _File:
    """One Lean file: its text, the lexed spans, the code view and the command-start lines."""

    def __init__(self, text: str) -> None:
        if text.startswith("﻿"):
            text = text[1:]
        text = text.replace("\r\n", "\n")
        self.text = text
        #: the offset of each line's first code point, for :meth:`line_of`
        self.line_starts = [0] + [m.end() for m in _NEWLINE.finditer(text)]
        self.spans = _lex(text)
        self.span_starts = [a for a, _b, _k in self.spans]
        self.code = _code_view(text, self.spans)
        self.comments = [(a, b, k) for a, b, k in self.spans if k in _COMMENT_KINDS]
        self.starts = self._command_starts()

    def span_at(self, i: int) -> tuple[int, int, str] | None:
        """The comment or literal span containing offset ``i``, if any."""
        k = bisect.bisect_right(self.span_starts, i) - 1
        if k >= 0 and self.spans[k][1] > i:
            return self.spans[k]
        return None

    def column(self, line_start: int) -> int:
        """The indentation of the line at ``line_start``: its leading whitespace, in code points.

        A line that begins inside a comment or literal has column 0.
        """
        if self.span_at(line_start) is not None:
            return 0
        i = line_start
        text = self.text
        while i < len(text) and text[i] in " \t\r":
            i += 1
        return i - line_start

    def _command_starts(self) -> list[tuple[int, int, int]]:
        """Every command-start line as ``(first offset, column, line start)``, in order.

        A line starts a command when, after its leading whitespace, it holds a
        doc comment (``/--`` or ``/-!``) or a token that is ``@[``, a
        ``#``-command, a modifier or a command keyword.
        """
        out: list[tuple[int, int, int]] = []
        code, text = self.code, self.text
        pos, n = 0, len(text)
        while pos < n:
            end = code.find("\n", pos)
            end = n if end < 0 else end
            col = self.column(pos)
            first = pos + col
            span = self.span_at(first) if first < end else None
            if span is not None and span[0] == first and span[2] in ("doc", "moddoc"):
                out.append((first, col, pos))
            else:
                while first < end and code[first] in " \t\r":
                    first += 1
                m = _HEAD_TOKEN.match(code, first) if first < end else None
                if m and (m.group() == "@[" or m.group()[0] == "#" or m.group() in _COMMAND_WORDS):
                    out.append((first, col, pos))
            pos = end + 1
        return out

    def limit(self, keyword_at: int, col: int) -> int:
        """Where the command holding the keyword at ``keyword_at`` ends at the latest.

        The first offset of the first command-start line after the keyword's
        line whose column is <= ``col``; the end of the text when there is none.
        """
        line_end = self.code.find("\n", keyword_at)
        if line_end < 0:
            return len(self.code)
        starts = self.starts                    # indexed, not sliced: a slice per declaration is quadratic
        for j in range(bisect.bisect_right(starts, (line_end, float("inf"), float("inf"))), len(starts)):
            if starts[j][1] <= col:
                return starts[j][0]
        return len(self.code)

    def head(self, first: int, targets: frozenset[str]) -> tuple[int, int] | None:
        """``(head start, keyword offset)`` of the command head at ``first`` if its keyword is a target.

        A head is a sequence of attributes ``@[...]``, modifiers and one-line
        prefixes (``open ... in``), separated by whitespace, ending in the
        keyword.  ``first`` may point at leading whitespace or a comment.
        """
        code = self.code
        i, n = first, len(code)
        start = None
        while True:
            while i < n and code[i] in _WS:
                i += 1
            if i >= n:
                return None
            if start is None:
                start = i
            if code.startswith("@[", i):
                i = _balanced(code, i + 1, "[", "]")
                if i is None:
                    return None
                continue
            m = _HEAD_TOKEN.match(code, i)
            if m is None:
                return None
            w = m.group()
            if w in targets:
                return start, i
            if w in _MODIFIERS:
                i = m.end()
            elif w in _PREFIX_WORDS:
                i = _prefix_end(code, m.end())
                if i is None:
                    return None
            else:
                return None

    def heads(self, targets: frozenset[str]) -> list[tuple[int, int, int]]:
        """``(head start, keyword offset, column)`` of every command whose keyword is in ``targets``."""
        out: list[tuple[int, int, int]] = []
        resume = 0
        for first, _col, _ls in self.starts:
            if first < resume:
                continue
            found = self.head(first, targets)
            if found is None:
                continue
            start, k = found
            out.append((start, k, self.column(self.code.rfind("\n", 0, start) + 1)))
            resume = self.code.find("\n", k)
            resume = len(self.code) if resume < 0 else resume + 1
        return out

    def line_of(self, offset: int) -> int:
        """The 1-based line holding ``offset`` (a newline belongs to the line it ends)."""
        return bisect.bisect_right(self.line_starts, offset)


def _balanced(code: str, i: int, open_: str, close: str) -> int | None:
    """The offset after the bracket closing the ``open_`` at ``i``, or None."""
    depth = 0
    for k in range(i, len(code)):
        c = code[k]
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return k + 1
    return None


def _prefix_end(code: str, i: int) -> int | None:
    """The offset after the whole-word ``in`` that ends a one-line prefix, on the same line."""
    depth = 0
    n = len(code)
    while i < n and code[i] != "\n":
        c = code[i]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth = max(0, depth - 1)
        elif _IS_WORD(c):
            m = _WORD_RE.match(code, i)
            if depth == 0 and m.group() == "in":
                return m.end()
            i = m.end()
            continue
        i += 1
    return None


def _instance_named(code: str, i: int) -> bool:
    """Does a name follow the ``instance`` keyword ending at ``i``?

    An optional ``(priority := ...)`` group (parentheses balanced) is skipped.
    The instance is anonymous when what follows is a binder opener (``(``,
    ``{``, ``[``, ``⦃``), the type colon or the end of the text; anything
    else is a name.
    """
    n = len(code)
    while i < n and code[i] in _WS:
        i += 1
    if i < n and code[i] == "(":
        j = i + 1
        while j < n and code[j] in _WS:
            j += 1
        if code.startswith("priority", j) and not _IS_WORD(code[j + 8:j + 9] or " "):
            end = _balanced(code, i, "(", ")")
            i = n if end is None else end
            while i < n and code[i] in _WS:
                i += 1
    return i < n and code[i] not in ":({[⦃"


def _header_end(code: str, i: int, limit: int) -> tuple[int, bool, bool]:
    """``(end, typed, terminated)`` for the header whose keyword ends at ``i``.

    Reads the code view from ``i`` to ``limit``.  The first ``:`` at bracket
    depth 0 makes the header typed; the header then ends at the first ``:=``
    (skipping one for each ``let``-like binder inside the type), whole-word
    ``where`` or match-alternative line (a line whose first non-whitespace code
    point is ``|`` followed by whitespace) at depth 0.  A ``:=`` at depth 0
    before any ``:`` means an untyped header: the command runs to ``limit``.
    """
    depth, typed, pending, bar = 0, False, 0, False
    while i < limit:
        c = code[i]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0:
            if c == ":":
                if code.startswith(":=", i):
                    if typed and pending:
                        pending -= 1
                        bar = False
                        i += 2
                        continue
                    return (i, True, True) if typed else (limit, False, False)
                typed = True
            elif typed:
                if c == "|":
                    line_start = code.rfind("\n", 0, i) + 1
                    after = code[i + 1] if i + 1 < len(code) else "\n"
                    if not code[line_start:i].strip(_WS) and after in _WS:
                        return i, True, True
                    if pending:
                        bar = True
                elif c == "↦" or (c == "=" and code.startswith("=>", i)):
                    if pending and bar:
                        pending -= 1
                        bar = False
                elif _IS_WORD(c):
                    m = _WORD_RE.match(code, i)
                    w = m.group()
                    if w == "where":
                        return i, True, True
                    if w in _TYPE_BINDERS:
                        pending += 1
                        bar = False
                    i = m.end()
                    continue
            elif _IS_WORD(c):
                i = _WORD_RE.match(code, i).end()
                continue
        i += 1
    return limit, typed, False


@dataclass(frozen=True)
class AnonymousDecl:
    """One ``example`` or anonymous ``instance`` found in a file.

    ``signature`` is the display form; ``statement_sha256`` hashes its hash
    form.  ``typed`` says whether the header had a type colon (an untyped one
    hashes the whole command).  ``rejected_tag`` explains a comment above the
    declaration that looked like a declared-id tag but was refused (``None``
    when there was none): ``; ``-separated entries, each a stable code
    (``tag-not-alone``, ``not-a-declared-id``, ``wrong-keyword``,
    ``conflicting-tags``), a colon and free text.  ``comment_tags`` lists every
    backticked ``<keyword>:<slug>`` tag that opens a comment directly above,
    declared or refused alike.
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


def _comments_above(f: _File, head_start: int) -> list[tuple[int, int, str]]:
    """The run of comments directly above the head at ``head_start``, nearest first.

    Each one starts its own line and is separated from the next (and from the
    head) by whitespace only.  A doc comment that is the note body of a
    ``library_note`` or ``#adaptation_note`` command ends the run.
    """
    run: list[tuple[int, int, str]] = []
    edge = head_start
    text = f.text
    k = bisect.bisect_left(f.span_starts, edge) - 1
    while k >= 0:
        a, b, kind = f.spans[k]
        k -= 1
        if kind not in _COMMENT_KINDS or b > edge or text[b:edge].strip(_WS):
            break
        line_start = text.rfind("\n", 0, a) + 1
        if text[line_start:a].strip(_WS):
            break
        if kind == "doc" and _is_note_body(f, a):
            break
        run.append((a, b, kind))
        edge = a
    return run


def _is_note_body(f: _File, a: int) -> bool:
    """Is the doc comment at ``a`` the body of a ``library_note`` / ``#adaptation_note`` just before it?"""
    text = f.text
    q = a - 1
    while q >= 0 and text[q] in _WS:
        q -= 1
    if q < 0:
        return False
    span = f.span_at(q)
    if span is not None and span[2] in _COMMENT_KINDS:
        return False
    code, i = f.code, text.rfind("\n", 0, q) + 1        # the line's first token (5.2): skip whitespace
    while i < len(code) and code[i] in " \t\r":          # in S, where a comment before it is blank
        i += 1
    m = _HEAD_TOKEN.match(code, i)
    return m is not None and m.group() in _NOTE_COMMANDS


def _comment_body(text: str, a: int, b: int, kind: str) -> str:
    body = text[a:b]
    if kind == "line":
        return body[2:].strip(_WS)
    opener = 3 if kind in ("doc", "moddoc") else 2
    return body[opener:-2 if body.endswith("-/") else len(body)].strip(_WS)


def _tag_only(body: str) -> str | None:
    """The tag inside a body that is exactly one backticked token, else None."""
    if len(body) >= 2 and body[0] == "`" and body[-1] == "`":
        inner = body[1:-1]
        if "`" not in inner and not any(c in _WS for c in inner):
            return inner
    return None


def _tag_like(body: str) -> bool:
    return body.startswith("`example:") or body.startswith("`instance:")


def _declared_tag(f: _File, run: list[tuple[int, int, str]], keyword: str) -> tuple[str | None, str | None]:
    """``(declared id, rejection)`` from the comments directly above a declaration."""
    found: list[str] = []
    rejected: list[str] = []
    for a, b, kind in run:
        body = _comment_body(f.text, a, b, kind)
        if _tag_like(body):
            inner = _tag_only(body)
            if inner is None:
                first = body.split("\n", 1)[0][:60]
                rejected.append(f"tag-not-alone: {first!r}: a tag comment holds the tag and nothing else")
            elif not DECLARED_ID.fullmatch(inner):
                rejected.append(f"not-a-declared-id: {inner!r} is not a declared id (<keyword>:<slug>)")
            else:
                found.append(inner)
        if kind == "doc":
            for line in body.split("\n"):
                line = line.strip(_WS)
                if not line.startswith(_TAG_LINE):
                    continue
                value = line[len(_TAG_LINE):].strip(_WS)
                if DECLARED_ID.fullmatch(value):
                    found.append(value)
                else:
                    rejected.append(f"not-a-declared-id: {value!r} is not a declared id (<keyword>:<slug>)")
    for tag in found:
        if not tag.startswith(keyword + ":"):
            rejected.append(f"wrong-keyword: {tag!r} is on a {keyword}")
    found = [t for t in found if t.startswith(keyword + ":")]
    if len(set(found)) > 1:
        rejected.append(f"conflicting-tags: two different tags: {sorted(set(found))}")
        found = []
    return (found[0] if found else None), ("; ".join(rejected) or None)


_TAG_OPENING = re.compile(r"`((?:example|instance):[^` \t\r\n]+)`")


def _comment_tags(f: _File, run: list[tuple[int, int, str]]) -> tuple[str, ...]:
    """The ``<keyword>:<slug>`` tags that open the comments directly above a declaration."""
    found: list[str] = []
    for a, b, kind in run:
        m = _TAG_OPENING.match(_comment_body(f.text, a, b, kind))
        if m:
            found.append(m.group(1))
    return tuple(found)


_ANONYMOUS = frozenset(vocab.ANONYMOUS_KEYWORDS)
#: Keywords whose body follows the header without a ``:=``: no signature.
_NO_SIGNATURE = frozenset({"structure", "class", "inductive"})
#: Keywords whose declaration has a signature (docs/id-algorithm.md 7.3), and ``example``, cut the same way.
_SIGNATURE_KEYWORDS = frozenset({"theorem", "lemma", "def", "abbrev", "instance", "opaque", "axiom",
                                 "irreducible_def", "example"})


def scan_file(text: str, path: str) -> list[AnonymousDecl]:
    """Every ``example`` and anonymous ``instance`` in one Lean file, in line order."""
    f = _File(text)
    found: list[AnonymousDecl] = []
    for head_start, k, col in f.heads(_ANONYMOUS):
        kw = _WORD_RE.match(f.code, k).group()
        after = k + len(kw)
        if kw == "instance" and _instance_named(f.code, after):
            continue
        end, typed, _terminated = _header_end(f.code, after, f.limit(k, col))
        signature = normalize_statement(f.text[k:end])
        run = _comments_above(f, head_start)
        declared, rejected = _declared_tag(f, run, kw)
        found.append(AnonymousDecl(
            file=path, line=f.line_of(k), keyword=kw, statement_sha256=statement_sha256(signature),
            typed=typed, declared_id=declared, signature=signature, rejected_tag=rejected,
            comment_tags=_comment_tags(f, run)))
    return found


def signature_at(text: str, line: int, keyword: str) -> tuple[str, str] | None:
    """The display signature and statement sha256 of the ``keyword`` declaration on 1-based ``line``.

    The same cut as an anonymous declaration's, for a named or anonymous
    declaration alike.  ``keyword`` is ``theorem``, ``lemma``, ``def``,
    ``abbrev``, ``instance``, ``opaque``, ``axiom``, ``irreducible_def`` or
    ``example``; ``structure``, ``class`` and ``inductive`` have no signature
    (None); any other word raises ``ValueError``.  None also when no such
    declaration starts on that line, or when it has no typed header to cut:
    an untyped header, or no ``:=`` / ``where`` / match alternative before
    the next command.
    """
    if keyword in _NO_SIGNATURE:
        return None
    if keyword not in _SIGNATURE_KEYWORDS:
        raise ValueError(f"not a declaration keyword with a signature: {keyword!r}")
    f = _File(text)
    for _head_start, k, col in f.heads(frozenset({keyword})):
        if f.line_of(k) != line:
            continue
        end, typed, terminated = _header_end(f.code, k + len(keyword), f.limit(k, col))
        if not typed or not terminated:
            return None
        signature = normalize_statement(f.text[k:end])
        return signature, statement_sha256(signature)
    return None


def _path_key(path: str) -> bytes:
    """Repository-relative POSIX paths compare by UTF-16 code units."""
    return path.encode("utf-16-be", "surrogatepass")


def assign_ids(decls: list[AnonymousDecl]) -> dict[AnonymousDecl, tuple[str, str]]:
    """``{decl: (id, id_source)}`` for one companion's anonymous declarations.

    The caller passes the declarations of EVERY ``.lean`` file tracked at the
    companion's commit: ordinals are positional.  They are assigned in (path,
    line) order, paths compared by UTF-16 code units, among the statement-hash
    ids only: the second and later declarations whose id would be the same
    take ``~2``, ``~3`` ...  A declared id takes no ordinal and shifts none.
    """
    seen: dict[str, int] = {}
    out: dict[AnonymousDecl, tuple[str, str]] = {}
    for d in sorted(decls, key=lambda d: (_path_key(d.file), d.line)):
        if d.declared_id:
            out[d] = (d.declared_id, "declared")
            continue
        base = anonymous_id(d.keyword, d.statement_sha256)
        seen[base] = seen.get(base, 0) + 1
        out[d] = (anonymous_id(d.keyword, d.statement_sha256, seen[base]), "statement_hash")
    return out


def ordinal_ids(assigned: dict[AnonymousDecl, tuple[str, str]]) -> list[tuple[str, AnonymousDecl]]:
    """The ``~k`` ids in ``assigned``, each with its declaration, sorted by id.

    Each one needs an ``ordinal_assigned`` changelog entry (or a declared id on
    the declaration) before a document carrying it validates.
    """
    out = [(rid, d) for d, (rid, source) in assigned.items()
           if source == "statement_hash" and "~" in rid]
    return sorted(out, key=lambda pair: pair[0])
