"""The Python half of `verso_host.sh`: the Verso require, and the pins it may not move.

New code, for docs/proposals/informalize.md section 10 (the overlay's hovers).
`scripts/informalize/verso_host.sh` builds the literate HTML of one library with Verso
and cuts `pdf-overlay/verso.json` out of it; this file holds what it needs written or
compared, as `library_host.py` does for the library's build.

Standard library only, and it imports nothing from `cflib`: it is copied to the run host
beside the drivers, where `cflib` is not on the path (CLAUDE.md section 4, "the gate runs
out of tree").  Subcommands (each prints what it did; a failure goes to stderr, exit 2)::

    require --lakefile F --rev R    add `[[require]] name = "verso"` at R, or say it is there
    pins    --manifest F            {"<package>": "<rev>", ...} of lake-manifest.json
    moved   --before F --manifest F name every package whose rev changed, exit 1 if any
    targets --bundle F --out F      literate.toml: the modules the overlay's cards name

**Where the require goes.**  Before the library's own first `[[require]]`, never after:
Lake lets the last require of a shared dependency win, so a Verso appended at the end
moves the pins of the packages the library already builds against (its Mathlib's `Cli` and
`plausible`), and the library would be rebuilt against them.  `moved` is the check that it
did not happen: the Verso build is of the same tree the cards pin, or it is nothing.

**Which modules are rendered.**  Only the ones the cards name, through Verso's
`literate.toml` (`[[targets]] module = ...`).  A library of ten thousand modules would
otherwise be rendered whole for the dozen pages the overlay reads, and a statement is cut
from its own module's page.  A library that ships a `literate.toml` of its own keeps it:
`verso_host.sh` writes this one only where there is none.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: a Verso revision this writes into a lakefile: a tag, a branch or a commit
REV = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
VERSO_URL = "https://github.com/leanprover/verso.git"
REQUIRE = "[[require]]"


class HostError(RuntimeError):
    """What the helper cannot do, and why."""


def _read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HostError(f"cannot read {what} ({path}): {exc}") from None


def require_block(rev: str) -> str:
    """The lines a Verso require is."""
    return f'{REQUIRE}\nname = "verso"\ngit = "{VERSO_URL}"\nrev = "{rev}"\n\n'


def verso_rev(text: str) -> str | None:
    """The revision a lakefile's own Verso require names, or None when it has none."""
    for block in text.split(REQUIRE)[1:]:
        head = block.split("\n[", 1)[0]
        if re.search(r'^\s*name\s*=\s*"verso"\s*$', head, re.MULTILINE):
            found = re.search(r'^\s*rev\s*=\s*"([^"]*)"\s*$', head, re.MULTILINE)
            return found.group(1) if found else ""
    return None


def add_require(path: Path, rev: str) -> str:
    """Write the Verso require into a `lakefile.toml`, before its first require; what was
    done, for the log.  A lakefile that already requires Verso is left as it is, and
    refused when it names another revision: the build is of one Verso."""
    if not REV.match(rev):
        raise HostError(f"the Verso revision {rev!r} is not a tag, a branch or a commit")
    if not path.is_file() or path.is_symlink():
        raise HostError(
            f"no {path}: this library has no lakefile.toml, so the require cannot be "
            "written here; add it by hand, before the library's first require, and run "
            "this script again")
    text = _read(path, "the lakefile")
    found = verso_rev(text)
    if found == rev:
        return f"the lakefile already requires verso at {rev}"
    if found is not None:
        raise HostError(f"the lakefile requires verso at {found!r}, not {rev!r}; pass "
                        f"VERSO_REV={found} or take the require out")
    at = text.find(REQUIRE)
    out = (text[:at] + require_block(rev) + text[at:]) if at >= 0 else \
        (text.rstrip("\n") + "\n\n" + require_block(rev))
    path.write_text(out, encoding="utf-8")
    where = "before the first require" if at >= 0 else "at the end (it requires nothing else)"
    return f"wrote the verso {rev} require into {path.name} {where}"


def pins(manifest: Path) -> dict[str, str]:
    """`{package: rev}` of a `lake-manifest.json`."""
    try:
        value = json.loads(_read(manifest, "the lake manifest"))
    except json.JSONDecodeError as exc:
        raise HostError(f"{manifest} is not JSON: {exc.msg}") from None
    packages = value.get("packages") if isinstance(value, dict) else None
    if not isinstance(packages, list):
        raise HostError(f"{manifest} holds no packages list")
    out = {}
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise HostError(f"{manifest} holds a package without a name")
        out[package["name"]] = str(package.get("rev"))
    return out


def moved(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Every package of `before` that `after` pins at another revision, or drops."""
    out = []
    for name, rev in sorted(before.items()):
        if name not in after:
            out.append(f"{name}: {rev[:12]} -> gone")
        elif after[name] != rev:
            out.append(f"{name}: {rev[:12]} -> {after[name][:12]}")
    return out


#: a Lean module name, as a card's `location.module` carries one
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_'!?]*(?:\.[A-Za-z_][A-Za-z0-9_'!?]*)*\Z")
TOML_HEAD = ("# The modules the overlay's cards name, written by `cf informalize "
             "verso-command`'s\n# host script (verso_host.py targets): Verso renders these "
             "and no others.\n")


def card_modules(bundle: Path, root: str = "") -> list[str]:
    """The library's modules an annotation bundle's object ends are in, without repeats.

    With `root` (the library's own root module, which `verso_host.sh` reads from
    `library.tsv`), a module outside it is left out: a card may point into a dependency --
    a notion of the conventions is often a constant of Mathlib -- and rendering Mathlib
    here is not cheap, so such a card keeps the plain statement the export cut for it.
    """
    try:
        value = json.loads(_read(bundle, "the annotation bundle"))
    except json.JSONDecodeError as exc:
        raise HostError(f"{bundle} is not JSON: {exc.msg}") from None
    rows = value.get("annotations") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise HostError(f"{bundle} holds no annotations list")
    out: dict[str, None] = {}
    for row in rows:
        annotation = row.get("annotation") if isinstance(row, dict) else None
        for end in (annotation.get("object") or []) if isinstance(annotation, dict) else ():
            location = end.get("location") if isinstance(end, dict) else None
            module = location.get("module") if isinstance(location, dict) else None
            if isinstance(module, str) and MODULE.match(module.strip()):
                name = module.strip()
                if not root or name == root or name.startswith(root + "."):
                    out[name] = None
    if not out:
        raise HostError(f"no card of {bundle.name} names a Lean module of "
                        f"{root or 'the library'}, so there is nothing to render")
    return sorted(out)


def write_targets(bundle: Path, out: Path, root: str = "") -> list[str]:
    """Write Verso's `literate.toml` for the bundle's modules; the modules it names."""
    modules = card_modules(bundle, root)
    body = "".join(f'[[targets]]\nmodule = "{module}"\n\n' for module in modules)
    out.write_text(TOML_HEAD + "\n" + body, encoding="utf-8")
    return modules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verso_host.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("require")
    p.add_argument("--lakefile", required=True, type=Path)
    p.add_argument("--rev", required=True)
    p = sub.add_parser("pins")
    p.add_argument("--manifest", required=True, type=Path)
    p = sub.add_parser("moved")
    p.add_argument("--before", required=True, type=Path)
    p.add_argument("--manifest", required=True, type=Path)
    p = sub.add_parser("targets")
    p.add_argument("--bundle", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--root", default="", help="the library's root module; a card in another "
                                              "library's module is left out")
    args = parser.parse_args(argv)
    try:
        if args.command == "require":
            print(add_require(args.lakefile, args.rev))
        elif args.command == "pins":
            print(json.dumps(pins(args.manifest), sort_keys=True))
        elif args.command == "targets":
            modules = write_targets(args.bundle, args.out, args.root)
            print(f"{args.out.name}: {len(modules)} module(s) the cards name")
        else:
            try:
                was = json.loads(_read(args.before, "the pins this run started from"))
            except json.JSONDecodeError as exc:
                raise HostError(f"{args.before} is not JSON: {exc.msg}") from None
            lines = moved({k: str(v) for k, v in was.items()}, pins(args.manifest))
            for line in lines:
                print(line)
            return 1 if lines else 0
    except HostError as error:
        print(f"verso_host.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
