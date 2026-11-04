"""The Python half of `library_host.sh`: the canary's form and files, a toolchain's slug.

New code, for docs/proposals/informalize.md section 6.1 (stage I1 on the run host).
`scripts/informalize/library_host.sh` builds one library, probes it, checks the probe's
acceptance canary on the library's toolchain and dumps the dependencies; this file holds
what it needs parsed or rendered, as `scripts/corpus/universe_host.py` does for the
universe driver.  The operator's side imports `library_form` and `digest` from here too,
so the rule that picks the canary's form and the record of what ran are defined once.

Standard library only, and it imports nothing from `cflib`: it is copied to the run host
beside the drivers, where `cflib` is not on the path (CLAUDE.md section 4, "the gate runs
out of tree").  Subcommands (each prints one line; a failure goes to stderr, exit 2)::

    module-form   --checkout D --index I      module | plain
    render-canary --dir D --toolchain T --form module|plain
    slug          --toolchain T
    digest        --drivers D --list L        {"drivers/<path>": sha256, ..., "<list>": sha256}

**The canary's form.**  templates/corpus/canary/ is rendered with the module system's
header (`module`, `public section` ... `end`) or with none.  Under the module system a
theorem's proof body is private, which is the case the canary's `hiddenSorry` exists
for, so the form follows the library: `module` when any module file the probe's index
names opens with the `module` command (a library part-way through moving to the module
system hides the bodies of those modules), `plain` otherwise (a toolchain without the
module system cannot build the other form, and a library that does not use it never hides
a body).

**What ran.**  `digest` records the sha256 of every driver and of the library list as the
job reads them, which fetch-library compares with the `library/to-host/` it copied from.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable

if __package__:
    from corpus import probe_merge as _names
else:  # pragma: no cover - the host's `python3 -I library_host.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "corpus"))
    import probe_merge as _names

#: the two forms of the canary, and the header and footer each puts around its declarations
#: (`cf corpus probe-command --canary` prints the module form's)
FORMS = ("module", "plain")
MODULE_HEADER = "module\n\npublic section"
MODULE_FOOTER = "end"
#: an elan toolchain, as `scripts/corpus/universe.py` accepts one (`TOOLCHAIN` there)
TOOLCHAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._+-]*)?\Z")
_TOOLCHAIN_PREFIX = "leanprover/lean4:"
_FIRST_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_'!?]*")


class HostError(RuntimeError):
    """What the helper cannot do, and why."""


def lean_version(toolchain: str) -> str:
    """`leanprover/lean4:v4.34.0-rc2` -> `4.34.0-rc2` (`corpus.universe.lean_version`)."""
    text = toolchain.strip()
    if text.startswith(_TOOLCHAIN_PREFIX):
        text = text[len(_TOOLCHAIN_PREFIX):]
    if text[:1] == "v" and text[1:2].isdigit():
        text = text[1:]
    return text


def slug(toolchain: str) -> str:
    """The canary directory of a toolchain, `$ATLAS_ROOT/canary/<slug>/`, as
    `cf corpus probe-command --canary` names it: its Lean version, every character
    outside [A-Za-z0-9._-] as `-`."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", lean_version(toolchain)).strip("-") or "toolchain"


def first_command(text: str) -> str:
    """The first word of a Lean file outside comments: `--` to the end of the line and
    `/- ... -/`, which nests."""
    i, depth, n = 0, 0, len(text)
    while i < n:
        if text.startswith("/-", i):
            depth += 1
            i += 2
        elif depth and text.startswith("-/", i):
            depth -= 1
            i += 2
        elif depth:
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif text[i].isspace():
            i += 1
        else:
            match = _FIRST_WORD.match(text, i)
            return match.group(0) if match else text[i]
    return ""


def module_form(text: str) -> str:
    """`module` when a Lean source opens with the `module` command, else `plain`."""
    return "module" if first_command(text) == "module" else "plain"


def library_form(texts: Iterable[str]) -> str:
    """The canary's form for a library whose module files hold `texts`: `module` when any
    of them opens with the `module` command, else `plain`."""
    return "module" if any(module_form(text) == "module" for text in texts) else "plain"


def index_module_paths(index: Path) -> list[str]:
    """The source paths of the module records of a probe's `index.jsonl`, each relative."""
    out = []
    with open(index, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line) if line.strip() else None
            if not isinstance(record, dict) or record.get("record") != "module":
                continue
            path = record.get("path")
            pure = PurePosixPath(path) if isinstance(path, str) else None
            if pure is None or pure.is_absolute() or ".." in pure.parts:
                raise HostError(f"{index} names the module file {path!r}, not a relative path")
            out.append(path)
    if not out:
        raise HostError(f"{index} names no module")
    return sorted(set(out))


def _sha256(path: Path) -> str:
    digest_ = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest_.update(block)
    return digest_.hexdigest()


def digest(drivers: Path, library_list: Path) -> dict[str, str]:
    """The sha256 of every ordinary file under `drivers`, as `drivers/<path>`, and of the
    library list, by its name: the keys `cf informalize build-command` writes them under in
    `library/to-host/`.  Python's byte-code caches, which running the drivers may write
    beside them, and links are left out."""
    out = {}
    for parent, names, files in os.walk(drivers):
        names[:] = [n for n in names if n != "__pycache__"]
        for name in files:
            path = Path(parent) / name
            if name.endswith(".pyc") or path.is_symlink() or not path.is_file():
                continue
            out[f"drivers/{path.relative_to(drivers).as_posix()}"] = _sha256(path)
    out[library_list.name] = _sha256(library_list)
    return dict(sorted(out.items()))


def module_source(checkout: Path, module: str) -> Path:
    """The source file of `module` in a checkout, as Lake names it."""
    if not _names.module_ok(module):
        raise HostError(f"{module!r} is not a module name")
    return checkout / _names.module_file(module)


def render_canary(directory: Path, toolchain: str, form: str) -> list[str]:
    """Fill the placeholders of the canary's `*.tmpl` files in `directory` and drop the
    suffix; the files written, relative to `directory`."""
    if not TOOLCHAIN.match(toolchain):
        raise HostError(f"{toolchain!r} is not an elan toolchain")
    if form not in FORMS:
        raise HostError(f"form {form!r} is not one of {', '.join(FORMS)}")
    header, footer = (MODULE_HEADER, MODULE_FOOTER) if form == "module" else ("", "")
    written = []
    templates = sorted(directory.rglob("*.tmpl"))
    if not templates:
        raise HostError(f"{directory} holds no canary template")
    for template in templates:
        text = template.read_text(encoding="utf-8")
        text = (text.replace("@@TOOLCHAIN@@", toolchain).replace("@@MODULE_HEADER@@", header)
                .replace("@@MODULE_FOOTER@@", footer))
        target = template.with_suffix("")
        target.write_text(text, encoding="utf-8")
        template.unlink()
        written.append(target.relative_to(directory).as_posix())
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("module-form")
    p.add_argument("--checkout", required=True, type=Path)
    p.add_argument("--index", required=True, type=Path)
    p = sub.add_parser("render-canary")
    p.add_argument("--dir", required=True, type=Path)
    p.add_argument("--toolchain", required=True)
    p.add_argument("--form", required=True)
    p = sub.add_parser("slug")
    p.add_argument("--toolchain", required=True)
    p = sub.add_parser("digest")
    p.add_argument("--drivers", required=True, type=Path)
    p.add_argument("--list", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "module-form":
            texts = []
            for path in index_module_paths(args.index):
                source = args.checkout / path
                if source.is_symlink() or not source.is_file():
                    raise HostError(f"the index names {path}, which {args.checkout} does not hold")
                texts.append(source.read_text(encoding="utf-8", errors="replace"))
            print(library_form(texts))
        elif args.command == "render-canary":
            print(" ".join(render_canary(args.dir, args.toolchain, args.form)))
        elif args.command == "digest":
            if not args.drivers.is_dir() or not args.list.is_file():
                raise HostError(f"no drivers folder {args.drivers} or library list {args.list}")
            print(json.dumps(digest(args.drivers, args.list), sort_keys=True))
        else:
            if not TOOLCHAIN.match(args.toolchain):
                raise HostError(f"{args.toolchain!r} is not an elan toolchain")
            print(slug(args.toolchain))
        return 0
    except (HostError, _names.ProbeError, OSError, ValueError) as error:
        print(f"library_host: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
