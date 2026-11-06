"""The files `cf informalize init` renders once into an output repository.

New code, for docs/proposals/informalize.md sections 4 and 7.1.  Two kinds:

* the project templates under `templates/informalize/project/` (`main.tex`,
  `notation.sty`, `sections/00-conventions.tex`, `STYLE-project.md` into `informal/`,
  `README.md` and `gitignore` as the root's `README.md` and `.gitignore`), rendered with
  `cflib.templates.render` from the slots `templates/informalize/README.md` lists;
* the license of the text, `LICENSE`: the text of `text.license` from
  `templates/informalize/licenses/<SPDX id>.txt`, one per license `pdf.publish` admits
  (`overlay.config.OPEN_LICENSES`); only MIT's has slots (the year and the holder).

Each is written only when it is absent: from then on it is the project's, and neither a
second `init` nor `sync` touches it.  `LICENSE` is the one exception: `init --force` with
another `text.license` writes the new license's text, since the file is nothing but that
choice.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from cflib.errors import CfError
from cflib.paths import templates_dir
from cflib.templates import render
from overlay.config import OPEN_LICENSES

from informalize.config import library_name

#: the license's name in a sentence, in Markdown and in LaTeX
LICENSE_NAMES = {
    "Apache-2.0": ("the Apache License, Version 2.0", "the Apache License, Version~2.0"),
    "CC-BY-4.0": ("the Creative Commons Attribution 4.0 International License",
                  "the Creative Commons Attribution 4.0 International License"),
    "CC-BY-SA-4.0": ("the Creative Commons Attribution-ShareAlike 4.0 International License",
                     "the Creative Commons Attribution-ShareAlike 4.0 International License"),
    "CC0-1.0": ("the CC0 1.0 Universal public domain dedication",
                "the CC0~1.0 Universal public domain dedication"),
    "MIT": ("the MIT License", "the MIT License"),
}
assert tuple(LICENSE_NAMES) == OPEN_LICENSES

#: where each project template lands, relative to the repository
DESTINATIONS = {"README.md": "README.md", "gitignore": ".gitignore"}

_TEX_SPECIAL = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
                "#": r"\#", "^": r"\textasciicircum{}", "_": r"\_", "%": r"\%",
                "~": r"\textasciitilde{}"}


def project_root() -> Path:
    return templates_dir() / "informalize" / "project"


def licenses_root() -> Path:
    return templates_dir() / "informalize" / "licenses"


def tex_escape(text: str) -> str:
    """Plain text as LaTeX source that prints it: `A & B_1` -> `A \\& B\\_1`."""
    return "".join(_TEX_SPECIAL.get(ch, ch) for ch in text)


def slots(cfg: dict) -> dict[str, str]:
    """The values of the project templates' slots (templates/informalize/README.md)."""
    library = cfg["library"]
    title, author, license_ = cfg["text"]["title"], cfg["text"]["author"], cfg["text"]["license"]
    name = library_name(library["url"])
    work = (cfg.get("reference") or {}).get("work")
    markdown_name, tex_name = LICENSE_NAMES[license_]
    if work:
        bibitem = rf"\bibitem{{reference}} {tex_escape(work)}."
        rules = (f"The text cites {work} by number only, through `\\fcref`: never a sentence "
                 "of the work, and never a close paraphrase. `\\fcref{<number>}` is for a number "
                 "the library's docstrings give, `\\fcref*{<number>}`, with a dagger, for one "
                 "inferred from the work's order (`items.json`, each `reference.source`).")
    else:
        bibitem = ""
        rules = "The text cites no reference work and uses no `\\fcref`."
    return {
        "title_tex": tex_escape(title),
        "author_tex": tex_escape(author),
        "license_tex": tex_name,
        "library_name_tex": tex_escape(name),
        "library_url": library["url"],
        "library_commit": library["commit"],
        "reference_bibitem_tex": bibitem,
        "title": title,
        "theorem": library["theorem"],
        "library_name": name,
        "license_name": markdown_name,
        "reference_rules": rules,
    }


def project_files(cfg: dict) -> dict[str, bytes]:
    """``{path relative to the repository: rendered bytes}`` of every project template."""
    values = slots(cfg)
    out: dict[str, bytes] = {}
    root = project_root()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative or relative.endswith(".pyc"):
            continue
        destination = DESTINATIONS.get(relative, f"informal/{relative}")
        out[destination] = render(path.read_text(encoding="utf-8"), values).encode("utf-8")
    return out


def license_text(cfg: dict, *, year: int | None = None) -> bytes:
    """`LICENSE`: the text of the text's license, with MIT's year and holder filled in."""
    license_ = cfg["text"]["license"]
    path = licenses_root() / f"{license_}.txt"
    if license_ not in OPEN_LICENSES or not path.is_file():
        raise CfError(f"no license text for {license_!r}",
                      hint=f"templates/informalize/licenses/ holds {', '.join(OPEN_LICENSES)}")
    text = path.read_text(encoding="utf-8")
    if re.search(r"\{\{", text):
        text = render(text, {"year": year or datetime.now(timezone.utc).year,
                             "holder": cfg["text"]["author"]})
    return text.encode("utf-8")


def license_matches(cfg: dict, raw: bytes | None) -> bool:
    """Is `raw` the text of the configured license (any year in MIT's copyright line)?"""
    if raw is None:
        return False
    if cfg["text"]["license"] != "MIT":
        return raw == license_text(cfg)
    pattern = re.escape(license_text(cfg, year=1).decode("utf-8")).replace(
        re.escape("(c) 1 "), r"\(c\) [0-9]{4} ")
    return re.fullmatch(pattern, raw.decode("utf-8", "replace")) is not None
