"""Write annotation records as Literature Links folder defaults and minimal leaves.

:func:`files` puts what records share in `_defaults.json` files and writes
each record as the patch over the defaults above it; it names no member.  A
side's ends share an end template (Literature Links 2.0, "Folder defaults"):
the members the ends of its commonest document share, when that document
holds a strict majority of the side's ends.  An end of that document is
written as the patch over the template, or whole, with its own ``uri``, when
the patch would have to remove a template member; a record whose side is the
template alone leaves the side out.  References compare by meaning: they are
resolved first, and each file gets them relative to itself (`companion.rebase`
says which strings they are).  The files are read back with the vendored
reader before they are returned.
"""
from __future__ import annotations

import copy
import json
import posixpath
import tempfile
import urllib.parse
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from cflib import vendored  # noqa: F401  (vendor/ on sys.path)
from literature_links import companion, discovery

__all__ = ["DEFAULTS", "END", "SIDES", "TOP", "canonical", "common", "dumps", "end_patch", "files", "moved", "patch",
           "subtract", "template_for", "written"]

DEFAULTS = discovery.DEFAULTS
SIDES = ("subject", "object")
#: Top-level members in this order, then the rest sorted; an end's in :data:`END` order; elsewhere `type` first.
TOP = ("$schema", "id", "kind", "status", "relation", "note", "subject", "object", "extensions")
END = ("type", "uri", "version", "location", "title", "role", "junk", "sha256", "citation", "identifiers", "extensions")
#: The annotation directory as a URL, deep enough that no reference climbs out of it.
_HOST = "https://annotations.invalid/"
_ROOT = _HOST + "%00/" * 64
_MARK = "\x00/" + DEFAULTS


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# factoring
# ---------------------------------------------------------------------------

def common(values: list[dict], atomic: Iterable[str] = ()) -> dict:
    """What every value shares: a member equal in all, or one in a strict majority whose values are objects.

    A member named in ``atomic`` is shared only whole.
    """
    out: dict = {}
    for key in dict.fromkeys(k for value in values for k in value):
        present = [value[key] for value in values if key in value]
        if len(present) == len(values) and all(_same(v, present[0]) for v in present):
            out[key] = copy.deepcopy(present[0])
        elif key not in atomic and 2 * len(present) > len(values) and all(isinstance(v, dict) for v in present):
            shared = common(present)
            if shared:
                out[key] = shared
    return out


def subtract(shared: dict, inherited: dict) -> dict:
    """``shared`` without what ``inherited`` already gives."""
    out: dict = {}
    for key, value in shared.items():
        if key in inherited and _same(inherited[key], value):
            continue
        if key in inherited and isinstance(value, dict) and isinstance(inherited[key], dict):
            rest = subtract(value, inherited[key])
            if rest:
                out[key] = rest
        else:
            out[key] = copy.deepcopy(value)
    return out


def patch(inherited: dict, record: dict) -> dict:
    """The smallest value that `companion.merge` turns ``inherited`` into ``record`` with."""
    out: dict = {key: None for key in inherited if key not in record}
    for key, value in record.items():
        if key not in inherited:
            out[key] = copy.deepcopy(value)
        elif _same(inherited[key], value):
            continue
        elif isinstance(value, dict) and isinstance(inherited[key], dict):
            out[key] = patch(inherited[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _has_null(value: Any) -> bool:
    return value is None or isinstance(value, dict) and any(_has_null(v) for v in value.values())


def template_for(ends: list) -> dict | None:
    """The end template of one side: what the ends of the commonest document share, or None.

    The document must hold a strict majority of the ends, two at least; a
    ``location`` is shared only whole, since an end's replaces the template's.
    """
    groups: dict[str, list[dict]] = {}
    for end in ends:
        if isinstance(end, dict) and isinstance(end.get("uri"), str):
            groups.setdefault(end["uri"], []).append(end)
    if not groups:
        return None
    group = max(groups.values(), key=len)
    if len(group) < 2 or 2 * len(group) <= len(ends):
        return None
    template = common(group, atomic=("location",))
    return template if "type" in template and "uri" in template else None


def end_patch(template: dict, end: dict) -> dict:
    """What an end with no ``uri`` writes to read as ``end`` over ``template``: a ``location`` replaces whole."""
    out: dict = {key: None for key in template if key not in end}
    for key, value in end.items():
        if key == "uri" or key in template and _same(template[key], value):
            continue
        if key != "location" and isinstance(value, dict) and isinstance(template.get(key), dict):
            out[key] = patch(template[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


# ---------------------------------------------------------------------------
# references
# ---------------------------------------------------------------------------

def _strings(value: Any, where: tuple = ()) -> Iterable[tuple[tuple, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, where + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, where + (index,))
    elif isinstance(value, str):
        yield where, value


def _put(value: Any, where: tuple, text: str) -> None:
    for step in where[:-1]:
        value = value[step]
    value[where[-1]] = text


def canonical(record: dict, path: str) -> dict:
    """``record`` with each relative reference resolved against its file ``path`` (in the annotation directory)."""
    out = copy.deepcopy(record)
    moved_ = dict(_strings(companion.rebase(record, _MARK)))
    for where, text in _strings(record):
        if moved_.get(where) != text:
            url = urllib.parse.urljoin(_ROOT + path, text)
            if not url.startswith(_ROOT[:len(_HOST) + 4]):
                raise ValueError(f"{path}: the reference {text!r} climbs too far")
            _put(out, where, url)
    return out


def _relative(url: str, path: str) -> str:
    base = urllib.parse.urlsplit(_ROOT + path).path.split("/")[:-1]
    split = urllib.parse.urlsplit(url)
    parts = split.path.split("/")
    shared = 0
    while shared < min(len(base), len(parts) - 1) and base[shared] == parts[shared]:
        shared += 1
    reference = "../" * (len(base) - shared) + "/".join(parts[shared:])
    if not reference or ":" in reference.split("/", 1)[0]:
        reference = "./" + reference
    reference += ("?" + split.query if split.query else "") + ("#" + split.fragment if split.fragment else "")
    if urllib.parse.urljoin(_ROOT + path, reference) != url:
        raise ValueError(f"{path}: cannot refer to {url} from here")
    return reference


def written(value: Any, path: str) -> Any:
    """``value`` with each resolved reference made relative to the file ``path``."""
    if isinstance(value, dict):
        return {key: written(item, path) for key, item in value.items()}
    if isinstance(value, list):
        return [written(item, path) for item in value]
    if isinstance(value, str) and value.startswith(_ROOT[:len(_HOST)]):
        return _relative(value, path)
    return value


def moved(record: dict, source: str, path: str) -> dict:
    """``record``, written for the file ``source``, with its relative references rewritten for ``path``."""
    return written(canonical(record, source), path)


# ---------------------------------------------------------------------------
# the bytes
# ---------------------------------------------------------------------------

def _scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _dump(value: Any, indent: str, first: tuple[str, ...]) -> str:
    inner = indent + "  "
    if isinstance(value, dict) and value:
        keys = [key for key in first if key in value] + sorted(key for key in value if key not in first)
        return "{\n" + ",\n".join(f"{inner}{_scalar(key)}: "
                                  f"{_dump(value[key], inner, END if first is TOP and key in SIDES else ('type',))}"
                                  for key in keys) + "\n" + indent + "}"
    if isinstance(value, list) and value:
        if not any(isinstance(item, (dict, list)) for item in value):
            return "[" + ", ".join(_scalar(item) for item in value) + "]"
        return "[\n" + ",\n".join(inner + _dump(item, inner, first) for item in value) + "\n" + indent + "]"
    return _scalar(value)


def dumps(value: dict) -> bytes:
    """One file: :data:`TOP` order, ends in :data:`END` order, two-space indent, UTF-8, arrays of scalars on
    one line, a final newline."""
    return (_dump(value, "", TOP) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# the files
# ---------------------------------------------------------------------------

def _inside(path: str, folder: str) -> bool:
    return not folder or path.startswith(folder + "/")


def _rest(value: dict) -> dict:
    return {key: item for key, item in value.items() if key not in SIDES}


def _written_record(inherited: dict, record: dict) -> dict:
    """``record`` as written under the defaults ``inherited``: the patch, each side over its template."""
    out = patch(_rest(inherited), _rest(record))
    for side in SIDES:
        ends, held = record[side], inherited.get(side)
        if not (isinstance(held, dict) and isinstance(held.get("uri"), str)):
            out[side] = copy.deepcopy(ends)
        elif len(ends) == 1 and _same(ends[0], held):
            continue                                      # the side is the template alone
        else:
            written = []
            for end in ends:
                filled = end_patch(held, end) if isinstance(end, dict) and end.get("uri") == held["uri"] else None
                # An end that would cancel a template member names its document and is written whole.
                written.append(copy.deepcopy(end) if filled is None or _has_null(filled) else filled)
            out[side] = written
    return out


def files(records: dict[str, dict], folders: Iterable[str]) -> dict[str, bytes]:
    """``{path: bytes}``: the defaults files and leaves the reader reads ``records`` back from.

    ``records`` maps each leaf's path in the annotation directory to its
    record, relative references resolved against that path.  Each folder of
    ``folders`` (``""`` is the directory) gets a `_defaults.json` holding what
    two or more records below it share, less what the folders above give, and
    an end template for each side whose ends mostly name one document.
    Raises ValueError, naming the files, when the reader would read anything else.
    """
    want = {path: canonical(record, path) for path, record in records.items()}
    given: dict[str, dict] = {}

    def inherited(path: str) -> dict:
        out: dict = {}
        for folder in sorted((f for f in given if _inside(path, f)), key=len):
            out = companion.merge(out, given[folder], defaults=True)
        return out

    for folder in sorted(set(folders), key=lambda f: (f.count("/") + bool(f), f)):
        below = [record for path, record in want.items() if _inside(path, folder)]
        if len(below) < 2:
            continue
        above = inherited(posixpath.join(folder, DEFAULTS))
        value = subtract(common([_rest(record) for record in below]), _rest(above))
        for side in SIDES:
            template = template_for([end for record in below for end in record[side]])
            held = above.get(side)
            if template is None or isinstance(held, dict) and _same(held, template):
                continue
            value[side] = patch(held, template) if isinstance(held, dict) else template
        if value:
            given[folder] = value
    out = {posixpath.join(folder, DEFAULTS): dumps(written(value, posixpath.join(folder, DEFAULTS)))
           for folder, value in given.items()}
    out.update({path: dumps(written(_written_record(inherited(path), record), path))
                for path, record in want.items()})
    out = dict(sorted(out.items()))
    _check(out, want)
    return out


def _check(out: dict[str, bytes], want: dict[str, dict]) -> None:
    """Read ``out`` with the vendored reader; every record must be the one wanted."""
    with tempfile.TemporaryDirectory(prefix="cf-overlay-defaults-") as tmp:
        root = Path(tmp).resolve()
        for name, raw in out.items():
            path = root.joinpath(*PurePosixPath(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        found = discovery.collect([root])
        problems = [f"{p.path.replace(str(root) + '/', '')}: {p.message} [{p.code}]" for p in found.problems]
        read = {record.path.relative_to(root).as_posix(): record.annotation for record in found.records}
    wrong = sorted(path for path in want.keys() | read.keys()
                   if path not in read or path not in want or not _same(canonical(read[path], path), want[path]))
    if problems or wrong:
        raise ValueError("the folder defaults do not read back as the records: "
                         + "; ".join(problems[:3] + [f"{path} differs" for path in wrong[:5]]))
