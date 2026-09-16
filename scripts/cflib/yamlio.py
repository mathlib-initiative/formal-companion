"""YAML load/dump with a stdlib-only fallback.

New code. PyYAML is present on this machine (6.0.3) and is used when
importable; the fallback parser covers the subset the skill writes itself
(campaign.yaml, adapter.yaml, run.yaml, tend task yaml): nested block
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


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
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


def _unescape(text: str) -> str:
    """Handle the double-quoted escapes the skill's own files use."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
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
        if _ANCHOR_RE.search(content):
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


def _block_scalar(text: str, start_line: int, indent: int, fold: bool) -> str:
    out = []
    for raw in text.splitlines()[start_line:]:
        if raw.strip() and (len(raw) - len(raw.lstrip())) <= indent:
            break
        out.append(raw[indent + 2:] if len(raw) > indent + 2 else raw.strip())
    while out and not out[-1].strip():
        out.pop()
    return (" ".join(x.strip() for x in out) if fold else "\n".join(out) + "\n") if out else ""


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
            out[key] = _block_scalar(text, lineno, ind, fold=val.startswith(">"))
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
        if rest == "":
            if i < len(lines) and lines[i][0] > ind:
                item, i = _parse_block(lines, i, lines[i][0], where, text)
                out.append(item)
            else:
                out.append(None)
        elif _KEY_RE.match(rest) and not rest.startswith(("\"", "'")):
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
            if isinstance(v, str) and "\n" in v:
                out.append(f"{pad}{k}: |\n" + _block_lines(v, indent + 2))
            elif isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:\n{dump_yaml(v, indent + 2)}")
            else:
                out.append(f"{pad}{k}: {_fmt_scalar(v)}\n")
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
            elif isinstance(v, str) and "\n" in v:
                out.append(f"{pad}- |\n" + _block_lines(v, indent + 2))
            else:
                out.append(f"{pad}- {_fmt_scalar(v)}\n")
        return "".join(out)
    return pad + _fmt_scalar(obj) + "\n"


def _block_lines(text: str, indent: int) -> str:
    """Render a multi-line string as an indented literal block scalar body."""
    pad = " " * indent
    body = text[:-1] if text.endswith("\n") else text
    return "".join(f"{pad}{line}\n" if line else "\n" for line in body.split("\n"))


def _fmt_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, dict)):
        return "[]" if isinstance(v, list) else "{}"
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{}]|^\s|\s$|^[-?&*!|>%@`]", s) or s.lower() in {
        "true", "false", "null", "yes", "no", "on", "off", "~"
    } or re.fullmatch(r"[+-]?[\d.]+", s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def write_yaml(path: str | Path, obj: Any, header: str = "") -> Path:
    """Write a YAML file atomically, with an optional comment header."""
    from .jsonio import atomic_write

    return atomic_write(path, (header + dump_yaml(obj)) if header else dump_yaml(obj))
