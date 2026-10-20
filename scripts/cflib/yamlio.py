"""YAML load/dump with a stdlib-only fallback.

New code. PyYAML is present on this machine (6.0.3) and is used when
importable; the fallback parser covers the subset the skill writes itself
(workspace.yaml, registry.yaml, adapter.yaml, run.yaml, tend task yaml): nested block
mappings, block sequences, flow `[]`/`{}` collections of scalars, quoted and
bare scalars, `|`/`>` block scalars, comments and blank lines.  Anchors,
aliases, tags, multi-document streams and complex keys are NOT supported and
raise CfError rather than silently returning the wrong thing -- but only when
they appear in a *value* position outside a block scalar: prose inside a
``description: |`` body (which is most of what a tend task file is) is taken
verbatim, markdown emphasis and all.

One documented divergence: PyYAML resolves an unquoted ``2026-09-16`` to a
``datetime.date`` while the fallback returns the string, so the skill's own
templates quote every date.

The writer quotes every plain scalar either reader would type as anything but
that string -- a value or a mapping key: a number of any base or with an exponent
(``1.5e-3``), a bool or null word in any case, a date or timestamp, ``.inf`` and
``.nan``, PyYAML's merge ``<<`` and value ``=`` -- and writes a float as both
readers read a float back (``1.0e-05``, ``.inf``).  ``tests/test_yamlio_lookalikes.py``
checks it against PyYAML's own resolver table when PyYAML is importable.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import CfError

try:  # pragma: no cover - environment dependent
    import yaml as _pyyaml
except ImportError:  # pragma: no cover
    _pyyaml = None

HAVE_PYYAML = _pyyaml is not None


def load_yaml(text: str, *, prefer_pyyaml: bool = True, where: str = "<yaml>") -> Any:
    """Parse a YAML document. Uses PyYAML when available."""
    if prefer_pyyaml and _pyyaml is not None:
        try:
            return _pyyaml.safe_load(text)
        except Exception as exc:  # pragma: no cover - malformed input
            raise CfError(f"{where}: invalid YAML: {exc}") from exc
    return load_yaml_minimal(text, where=where)


def read_yaml(path: str | Path, *, prefer_pyyaml: bool = True) -> Any:
    """Parse a YAML file; raises CfError when it is missing."""
    p = Path(path)
    if not p.exists():
        raise CfError(f"missing file: {p}")
    return load_yaml(p.read_text(encoding="utf-8"), prefer_pyyaml=prefer_pyyaml, where=str(p))


# --------------------------------------------------------------------------
# minimal reader
# --------------------------------------------------------------------------

_KEY_RE = re.compile(r"^(?P<key>(?:\"[^\"]*\"|'[^']*'|[^:#]+?))\s*:(?:\s+(?P<val>.*))?$")
#: a sequence item that opens with a QUOTED key (``- "no": x``): the quoted part is the
#: whole key, so a quoted scalar item holding ``: `` (``- "a: b"``) is not one
_QUOTED_KEY_RE = re.compile(r"^(?:\"[^\"\\]*\"|'[^']*')\s*:(?:\s+.*)?$")


def _strip_comment(line: str) -> str:
    """Drop a trailing `` # comment``, but never one inside a quoted scalar.

    The backslash step is why this cannot be a ``split(" #")``: the writer escapes
    a quote mark inside a double-quoted scalar as ``\\"``, and reading that quote
    as the CLOSING one left the rest of the value looking like a comment.  A
    harvested title of the shape ``" #x\\`` -- a leading quote mark, a ` #`, and a
    trailing backslash -- then came back as just the backslash here while PyYAML
    read it whole, so the two readers disagreed about a file cf wrote itself.
    """
    out, quote, i, n = [], None, 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            out.append(ch)
            if quote == '"' and ch == "\\" and i + 1 < n:
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _scalar(tok: str) -> Any:
    tok = tok.strip()
    if tok == "" or tok in {"~", "null", "Null", "NULL"}:
        return None
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        inner = tok[1:-1]
        if tok[0] == "'":
            return inner.replace("''", "'")
        return _unescape(inner)
    low = tok.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    if re.fullmatch(r"[+-]?\d+", tok):
        return int(tok)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?", tok):
        return float(tok)
    # PyYAML's spellings of the infinities and NaN, which the writer uses for a float
    if re.fullmatch(r"[+-]?\.(?:inf|Inf|INF)", tok):
        return float("-inf") if tok[0] == "-" else float("inf")
    if re.fullmatch(r"\.(?:nan|NaN|NAN)", tok):
        return float("nan")
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        return [] if not inner else [_scalar(x) for x in _split_flow(inner)]
    if tok.startswith("{") and tok.endswith("}"):
        inner = tok[1:-1].strip()
        if not inner:
            return {}
        out = {}
        for part in _split_flow(inner):
            k, _, v = part.partition(":")
            out[str(_scalar(k))] = _scalar(v)
        return out
    return tok


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "/": "/"}

#: ``\xNN`` / ``\uNNNN`` / ``\UNNNNNNNN``, the numeric escapes PyYAML resolves.
_NUMERIC_ESCAPES = {"x": 2, "u": 4, "U": 8}


def _unescape(text: str) -> str:
    """Handle the double-quoted escapes the skill's own files use.

    The numeric forms are here because the writer emits them: a control
    character in a harvested book title is written as ``\\x1f`` and PyYAML
    resolves it, so the fallback reader has to resolve it too or the two readers
    disagree about the same file.
    """
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            width = _NUMERIC_ESCAPES.get(nxt)
            if width:
                digits = text[i + 2:i + 2 + width]
                if len(digits) == width and all(c in "0123456789abcdefABCDEF" for c in digits):
                    out.append(chr(int(digits, 16)))
                    i += 2 + width
                    continue
            out.append(_ESCAPES.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_flow(text: str) -> list[str]:
    parts, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


#: an anchor (``&name``) or alias (``*name``) in a *value* position
_ANCHOR_RE = re.compile(r"(?:^|:\s+|-\s+)[&*][A-Za-z_]")


def _mask_quoted(line: str) -> str:
    """*line* with the inside of every quoted scalar blanked, length preserved.

    The anchor/alias check runs on this rather than on the raw line.  A
    double-quoted value that happens to contain ``: &x`` is not an anchor, and the
    writer emits exactly that for a harvested title carrying a colon and an
    ampersand -- so the fallback reader used to refuse a file cf had just written
    while PyYAML read it back unchanged.
    """
    out, quote, i, n = [], None, 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            if quote == '"' and ch == "\\" and i + 1 < n:
                out.append("xx")
                i += 2
                continue
            out.append(ch if ch == quote else "x")
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
        out.append(ch)
        i += 1
    return "".join(out)

#: a value that opens a literal/folded block scalar: ``|``, ``|-``, ``>2+``, ...
_BLOCK_OPEN_RE = re.compile(r"(?:^|:\s*|-\s+)[|>][0-9]*[-+]?$")


def load_yaml_minimal(text: str, where: str = "<yaml>") -> Any:
    """Parse the YAML subset the skill writes. Stdlib only.

    The scanner tracks literal/folded block scalars (``description: |``) so that
    their bodies are taken verbatim: no comment stripping, no anchor check and
    no key grammar.  Without that, one line of the skill's own prose --
    ``*because of* a junk value`` in ``templates/prompts/attack-vac.md.tmpl`` --
    read as an alias and aborted the parse of every attack-vac task file.
    """
    lines: list[tuple[int, str, int]] = []  # (indent, content, lineno)
    block_indent: int | None = None  # indent of the key that opened a block scalar
    for n, raw in enumerate(text.splitlines(), 1):
        indent = len(raw) - len(raw.lstrip())
        if block_indent is not None:
            if not raw.strip():
                continue  # blank lines belong to the block body
            if indent > block_indent:
                # Inside the block scalar. The value itself is re-read from the
                # raw text by _block_scalar(); these rows exist only so the
                # parser can skip past them by indent.
                lines.append((indent, raw.strip(), n))
                continue
            block_indent = None  # dedent ends the block
        if raw.lstrip().startswith("%") or raw.strip() == "---":
            continue
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        content = stripped.strip()
        if _ANCHOR_RE.search(_mask_quoted(content)):
            raise CfError(f"{where}:{n}: YAML anchors/aliases are not supported by the fallback reader",
                          hint="install PyYAML (pip install pyyaml) or simplify the file")
        lines.append((indent, content, n))
        if _BLOCK_OPEN_RE.search(content):
            block_indent = indent
    if not lines:
        return None
    value, idx = _parse_block(lines, 0, lines[0][0], where, text)
    if idx != len(lines):
        raise CfError(f"{where}:{lines[idx][2]}: unexpected indentation")
    return value


def _block_scalar(text: str, start_line: int, indent: int, fold: bool, strip: bool = False) -> str:
    """The body of a ``|``/``>`` block scalar, read straight from the raw text.

    ``strip`` is the ``-`` chomping indicator.  Without it the fallback reader
    always appended the final newline that ``|`` implies, so a string the writer
    had emitted as ``|-`` came back one character longer than it went in.
    """
    out = []
    for raw in text.splitlines()[start_line:]:
        if raw.strip() and (len(raw) - len(raw.lstrip())) <= indent:
            break
        out.append(raw[indent + 2:] if len(raw) > indent + 2 else raw.strip())
    while out and not out[-1].strip():
        out.pop()
    if not out:
        return ""
    if fold:
        return " ".join(x.strip() for x in out)
    return "\n".join(out) if strip else "\n".join(out) + "\n"


def _parse_block(lines, i: int, indent: int, where: str, text: str):
    if lines[i][1].startswith("- "):
        return _parse_seq(lines, i, indent, where, text)
    if lines[i][1] == "-":
        return _parse_seq(lines, i, indent, where, text)
    return _parse_map(lines, i, indent, where, text)


def _parse_map(lines, i: int, indent: int, where: str, text: str):
    out: dict[str, Any] = {}
    while i < len(lines):
        ind, content, lineno = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise CfError(f"{where}:{lineno}: unexpected indentation in mapping")
        m = _KEY_RE.match(content)
        if not m:
            raise CfError(f"{where}:{lineno}: expected 'key: value', got {content!r}")
        key = str(_scalar(m.group("key")))
        val = (m.group("val") or "").strip()
        i += 1
        if val in {"|", "|-", ">", ">-"}:
            out[key] = _block_scalar(text, lineno, ind, fold=val.startswith(">"),
                                     strip=val.endswith("-"))
            while i < len(lines) and lines[i][0] > ind:
                i += 1
        elif val == "":
            if i < len(lines) and lines[i][0] > ind:
                out[key], i = _parse_block(lines, i, lines[i][0], where, text)
            elif i < len(lines) and lines[i][0] == ind and lines[i][1].startswith("-"):
                out[key], i = _parse_seq(lines, i, ind, where, text)
            else:
                out[key] = None
        else:
            out[key] = _scalar(val)
    return out, i


def _parse_seq(lines, i: int, indent: int, where: str, text: str):
    out: list[Any] = []
    while i < len(lines):
        ind, content, lineno = lines[i]
        if ind < indent or not content.startswith("-"):
            break
        if ind > indent:
            raise CfError(f"{where}:{lineno}: unexpected indentation in sequence")
        rest = content[1:].strip()
        i += 1
        if rest in {"|", "|-", ">", ">-"}:
            # `- |` is a shape the WRITER emits (a multi-line string inside a
            # list), and the reader used to take the indicator as the item's
            # value and then abort on the body's indentation.
            out.append(_block_scalar(text, lineno, ind, fold=rest.startswith(">"),
                                     strip=rest.endswith("-")))
            while i < len(lines) and lines[i][0] > ind:
                i += 1
        elif rest == "":
            if i < len(lines) and lines[i][0] > ind:
                item, i = _parse_block(lines, i, lines[i][0], where, text)
                out.append(item)
            else:
                out.append(None)
        elif _KEY_RE.match(rest) and (not rest.startswith(("\"", "'"))
                                      or _QUOTED_KEY_RE.match(rest)):
            # inline first key of a mapping item; following deeper lines continue it
            sub = [(ind + 2, rest, lineno)]
            while i < len(lines) and lines[i][0] > ind:
                sub.append(lines[i])
                i += 1
            item, _ = _parse_map(sub, 0, ind + 2, where, text)
            out.append(item)
        else:
            out.append(_scalar(rest))
    return out, i


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------

def dump_yaml(obj: Any, indent: int = 0) -> str:
    """Deterministic YAML writer for the same subset (insertion order kept)."""
    pad = " " * indent
    if isinstance(obj, dict):
        if not obj:
            return pad + "{}\n"
        out = []
        for k, v in obj.items():
            key = _fmt_key(k)
            if isinstance(v, str) and "\n" in v and _block_safe(v):
                out.append(f"{pad}{key}: {_block_head(v)}\n" + _block_lines(v, indent + 2))
            elif isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{key}:\n{dump_yaml(v, indent + 2)}")
            else:
                out.append(f"{pad}{key}: {_fmt_scalar(v)}\n")
        return "".join(out)
    if isinstance(obj, list):
        if not obj:
            return pad + "[]\n"
        out = []
        for v in obj:
            if isinstance(v, dict) and v:
                body = dump_yaml(v, indent + 2)
                out.append(pad + "- " + body[indent + 2:])
            elif isinstance(v, list) and v:
                out.append(f"{pad}-\n{dump_yaml(v, indent + 2)}")
            elif isinstance(v, str) and "\n" in v and _block_safe(v):
                out.append(f"{pad}- {_block_head(v)}\n" + _block_lines(v, indent + 2))
            else:
                out.append(f"{pad}- {_fmt_scalar(v)}\n")
        return "".join(out)
    return pad + _fmt_scalar(obj) + "\n"


def _block_lines(text: str, indent: int) -> str:
    """Render a multi-line string as an indented literal block scalar body."""
    pad = " " * indent
    body = text[:-1] if text.endswith("\n") else text
    return "".join(f"{pad}{line}\n" if line else "\n" for line in body.split("\n"))


def _block_head(text: str) -> str:
    """``|`` or ``|-``: a body that does not end in a newline must be chomped."""
    return "|" if text.endswith("\n") else "|-"


def _block_safe(text: str) -> bool:
    """Whether a literal block scalar reproduces *text* byte for byte.

    Three shapes it cannot: a first line that starts with whitespace (its indent
    would be read as the block's own, and PyYAML refuses the file outright), a
    body whose LAST line carries no non-space character, and any control character
    other than the line breaks.  Those go through the double-quoted writer
    instead, which is uglier but lossless -- and a workspace.yaml cf cannot read
    back is worse than an ugly line.

    The last-line rule covers both an empty final line (only ``|+`` keeps it, and
    neither reader implements ``+``) and one that is whitespace-only but NOT
    empty, which is the same defect wearing a disguise: ``"a\\n "`` passes every
    other check, is written as ``|-``, and the fallback reader's own trailing-blank
    chomp then drops it while PyYAML keeps it -- so the two readers disagreed about
    a file cf wrote itself.
    """
    body = text[:-1] if text.endswith("\n") else text
    if not body.split("\n")[-1].strip():
        return False
    if _CONTROL_RE.search(body.replace("\n", "")):
        return False
    first = next((line for line in body.split("\n") if line), "")
    return first == first.lstrip()


#: Characters a plain YAML scalar may not carry: C0 controls, DEL, the C1 block
#: (NEL is a line break in YAML 1.1), the Unicode line/paragraph separators and
#: the BOM.  PyYAML rejects the file rather than reading them back.
_CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f  ﻿]")

_CONTROL_NAMED = {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\0": "\\0"}

#: Anything a reader would type as a number rather than hand back as a string.
#: The fallback reader only understands decimals, so an unquoted `0x10` meant
#: 16 with PyYAML installed and "0x10" without it; and an exponent was missing,
#: so `1.5e-3` was written bare and read back as 0.0015.  PyYAML's own rules
#: (``yaml.resolver.Resolver``, YAML 1.1) are narrower -- an exponent needs a dot
#: and a sign, so `1e3` is a string to it but a float to a YAML 1.2 reader -- and
#: this is deliberately wider than both: digits, ``._``, an optional exponent and
#: sign, a base prefix, ``.inf`` and ``.nan``.  Base-60 numbers and timestamps
#: carry a ``:``, which is quoted on its own.
_NUMBER_LOOKALIKE = re.compile(
    r"[+-]?(?:[\d.][\d._]*(?:[eE][+-]?[\d_]*)?|0[xX][0-9a-fA-F_]+|0[oObB][0-7_]+"
    r"|\.(?:inf|Inf|INF|nan|NaN|NAN))")
#: Plain words a reader types, compared lower-cased: PyYAML's bools and nulls (the
#: fallback reader folds case, so ``yEs`` is quoted too), and its merge key ``<<``
#: and value ``=``, which it refuses to construct at all, so a bare one made the
#: whole file unreadable
_TYPED_WORDS = frozenset({"true", "false", "null", "yes", "no", "on", "off", "~", "<<", "="})
#: PyYAML reads an unquoted ``2026-09-16`` as a ``datetime.date``
_DATE_LOOKALIKE = re.compile(r"\d{4}-\d{2}-\d{2}")
#: the look-alikes that carry a ``:`` -- PyYAML's base-60 numbers (``1:30`` is 90) and
#: its timestamps -- which a value is quoted for anyway, but a mapping key is not
_COLON_LOOKALIKE = re.compile(
    r"[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?"
    r"(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?")


def _typed_plain(s: str) -> bool:
    """Would either reader type the plain scalar ``s`` as anything but this string?

    The one rule for both a value and a mapping key: PyYAML's implicit types
    (bool, int, float, null, timestamp, merge, value) and the fallback reader's
    (``_scalar``), as :data:`_NUMBER_LOOKALIKE`, :data:`_TYPED_WORDS`,
    :data:`_DATE_LOOKALIKE` and :data:`_COLON_LOOKALIKE` state them, and the empty
    string (a null to both).
    """
    return (s == "" or s.lower() in _TYPED_WORDS or _NUMBER_LOOKALIKE.fullmatch(s) is not None
            or _DATE_LOOKALIKE.fullmatch(s) is not None
            or _COLON_LOOKALIKE.fullmatch(s) is not None)


def _escape_control(match: "re.Match[str]") -> str:
    ch = match.group(0)
    if ch in _CONTROL_NAMED:
        return _CONTROL_NAMED[ch]
    code = ord(ch)
    return "\\x%02x" % code if code < 0x100 else "\\u%04x" % code


def _double_quoted(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + _CONTROL_RE.sub(_escape_control, escaped) + '"'


def _fmt_float(v: float) -> str:
    """A float both readers read back as this float.

    ``str()`` wrote ``1e-05``, ``1e+16`` and ``inf``, which PyYAML reads as strings
    (its float needs a dot, and an infinity is ``.inf``): the mantissa gains its
    ``.0`` and the infinities and NaN are written as YAML spells them.
    """
    if v != v:
        return ".nan"
    if v in (float("inf"), float("-inf")):
        return ".inf" if v > 0 else "-.inf"
    text = repr(v)
    mantissa, e, exponent = text.partition("e")
    if e:
        if "." not in mantissa:
            mantissa += ".0"
        if exponent[:1] not in "+-":
            exponent = "+" + exponent
        return f"{mantissa}e{exponent}"
    return text if "." in text else text + ".0"


def _fmt_key(k: Any) -> str:
    """A mapping key: bare, unless a reader would type it (``"no": ...``, ``"1.5e-3": ...``).

    Both readers typed such a key -- PyYAML to ``False`` or ``0.0015``, the fallback
    reader to the string ``"False"`` -- so a key cf wrote came back as another key.
    """
    s = str(k)
    if isinstance(k, str) and _typed_plain(s):
        return _double_quoted(s)
    return s


def _fmt_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return _fmt_float(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, dict)):
        return "[]" if isinstance(v, list) else "{}"
    s = str(v)
    #: an ISO date is quoted too: PyYAML types `2026-09-16` as a datetime.date
    #: while the fallback reader keeps a string, so an unquoted one makes
    #: workspace.yaml mean two different things depending on what is installed.
    #:
    #: A leading quote mark and a control character are quoted because titles are
    #: HARVESTED, not typed: a companion whose metadata says `"An Introduction"`
    #: or carries a tab used to be written out bare, and then Registry.load()
    #: raised `invalid YAML` on the workspace's own registry.yaml (or, worse,
    #: read the title back without its quotes).
    #: The leading set is every character YAML reserves as the FIRST one of a plain
    #: scalar. `,` is in it because a flow indicator cannot open a node even in
    #: block context: a title harvested as ", and other notes" was written bare and
    #: PyYAML then refused the whole file ("expected the node content, but found
    #: ','"), which is the same defect as the tab.
    if re.search(r"[:#\[\]{}]|^\s|\s$|^[-?&*!|>%@`,]", s) or _typed_plain(s) \
            or _CONTROL_RE.search(s) or s[:1] in "\"'":
        return _double_quoted(s)
    return s


def write_yaml(path: str | Path, obj: Any, header: str = "") -> Path:
    """Write a YAML file atomically, with an optional comment header."""
    from .jsonio import atomic_write

    return atomic_write(path, (header + dump_yaml(obj)) if header else dump_yaml(obj))
