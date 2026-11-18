r"""Adapter: a Markdown roadmap whose pages cite line ranges of a TeX manuscript.

New code, for the convention `references/onboarding-contract.md` section 1
describes under ``roadmap_locators`` and `docs/exporters.md` section 9 exports.
The companion formalizes a manuscript it holds as one TeX file, and writes the
map from statements to declarations in two kinds of Markdown file: a roadmap
**page** names its declarations in a ``lean:`` front matter and cites sections
of **locator files**, and a locator section gives line ranges, or a label, of
the TeX file.

The manuscript is the source, as a leanblueprint's is, so this kind is
`blueprint_tex` with one difference: the declarations of a statement are not a
``\lean{}`` inside its environment but the ``lean:`` names of the pages that
cite it.  Everything else is that adapter's -- the ``\newtheorem`` table, the
numbering, the locator that is ``number`` or ``label`` and never both, the
manuscript claimed as files, the rows -- and is inherited rather than written
again.

What makes a page's declarations rows of a statement:

* the page says ``origin: cited`` and ``statement: formalized``: its statement
  is the source's and it claims the Lean states it.  A page that bridges two
  source statements, or supplies background, cites the manuscript too, and its
  declarations state neither; they are counted and not attributed;
* a locator section the page cites names lines that take in the whole
  statement environment, or lie wholly inside it.  A passage that only touches
  an environment, or is running text, is counted (``passages_outside_statements``)
  and attributed to nothing: the audit compares a declaration with a statement,
  and a paragraph of a proof is not one.

A statement after ``\appendix`` is numbered with a letter, which the item
grammar does not read, so it is keyed on its label; and since a table is keyed
on numbers or on labels and never both, a companion that formalizes an
appendix statement is keyed on labels throughout.

``proof: formalized`` on a citing page is the companion's claim that the proof
is done, as ``\leanok`` in a ``proof`` environment is a leanblueprint's.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path, PurePosixPath
from typing import Mapping

from .api import AdapterError, register, sha_of_files
from .blueprint_tex import _TEX_MARKUP_RE, BlueprintTexAdapter

__all__ = ["RoadmapLocatorsAdapter", "BLUEPRINT", "DEFAULT_GLOBS"]

#: The directory that holds ``roadmap/`` and ``sources/``, and the pages under it.
BLUEPRINT = "blueprint"
DEFAULT_GLOBS = ("blueprint/roadmap/**/*.md",)
ROADMAP = "roadmap"
SOURCES = "sources"
#: The front matter values that make a page's declarations rows.
ORIGIN_CITED = "cited"
FORMALIZED = "formalized"
_APPENDIX_RE = re.compile(r"^\s*\\appendix\b", re.M)


def manuscript_title(text: str) -> str:
    r"""The argument of the file's ``\title``, its braces balanced and its markup dropped.

    A paper's title is often mathematics (``\title{$\mathbb{Z}$ is ...}``), and
    reading to the first closing brace would name the manuscript by one letter.
    """
    start = re.search(r"\\title\s*\{", text)
    if not start:
        return ""
    depth, at = 1, start.end()
    while at < len(text) and depth and at - start.end() < 400:
        depth += {"{": 1, "}": -1}.get(text[at], 0)
        at += 1
    if depth:
        return ""
    return re.sub(r"\s+", " ", _TEX_MARKUP_RE.sub(" ", text[start.end():at - 1])).strip()


def _reading():
    """The convention's reading rules, which live with the export that first needed them.

    Imported where it is used: `annotations.export` imports this package for its
    Lean parser, so the import cannot stand at the top of a module of it.
    """
    from annotations.export import roadmap_locators, texplace
    return roadmap_locators, texplace


def survey(repo: Path, blueprint: str = BLUEPRINT) -> dict:
    """The locator files and the pages of ``repo``, as the working tree has them.

    ``locators`` maps each locator file to what `read_locator` makes of it,
    ``tex`` lists the TeX files they say they read, and ``pages`` holds, for
    each roadmap page, its front matter and the locator sections it cites.
    """
    reading, _ = _reading()
    root = Path(repo)
    locators: dict[str, dict] = {}
    for path in sorted((root / blueprint / SOURCES).glob("*.md")):
        if path.name == "README.md" or not path.is_file():
            continue
        locators[path.relative_to(root).as_posix()] = reading.read_locator(
            path.read_text(encoding="utf-8", errors="replace"))
    pages = []
    for path in sorted((root / blueprint / ROADMAP).rglob("*.md")):
        if path.name == "README.md" or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        meta = reading.front_matter(text)
        sections, _no_tex, dangling = reading.cited_sections(rel, text, locators)
        pages.append({"rel": rel, "meta": meta, "sections": sections, "dangling": dangling,
                      "names": [n.strip() for n in meta.get("lean", "").split(",") if n.strip()]})
    return {"locators": locators, "pages": pages,
            "tex": sorted({loc["tex"] for loc in locators.values() if loc["tex"]})}


@register
class RoadmapLocatorsAdapter(BlueprintTexAdapter):
    """Companions whose item -> declaration map is a roadmap of pages citing a TeX manuscript."""

    kind = "roadmap_locators"

    # -- detection ---------------------------------------------------------
    #: score = BASE + SPAN * (pages with a `lean:` field that cite the manuscript
    #: / pages with a `lean:` field).  A roadmap most of whose pages cite only
    #: project notes scores low on purpose: there is little of the source in it.
    DETECT_BASE_SCORE = 0.6
    DETECT_SPAN = 0.35
    DETECT_MAX_SCORE = 0.95

    @classmethod
    def _measure(cls, repo: str | Path) -> tuple[int, int, int, list[str], int]:
        """``(pages, pages with lean:, of those citing the manuscript, TeX files named, locator files)``."""
        found = survey(Path(repo))
        with_lean = [p for p in found["pages"] if p["names"]]
        citing = [p for p in with_lean if p["sections"]]
        backed = sum(1 for loc in found["locators"].values() if loc["tex"])
        return len(found["pages"]), len(with_lean), len(citing), found["tex"], backed

    @classmethod
    def detect(cls, repo: str | Path) -> tuple[float, dict]:
        pages, with_lean, citing, tex, backed = cls._measure(repo)
        if not citing or len(tex) != 1:
            return 0.0, {}
        score = min(cls.DETECT_MAX_SCORE, cls.DETECT_BASE_SCORE + cls.DETECT_SPAN * citing / with_lean)
        return round(score, 4), {
            "reason": f"{citing} of {with_lean} roadmap pages with a lean: field cite {tex[0]} "
                      f"through {backed} locator file(s) under {BLUEPRINT}/{SOURCES}/",
            "pages": pages, "pages_with_lean": with_lean, "pages_citing_manuscript": citing,
            "tex": tex[0], "locator_files": backed,
        }

    @classmethod
    def explain(cls, repo: str | Path) -> list[str]:
        pages, with_lean, citing, tex, backed = cls._measure(repo)
        if not pages:
            return [f"roadmap locators: no {BLUEPRINT}/{ROADMAP}/**/*.md in this repo"]
        if len(tex) > 1:
            return [f"roadmap locators: the locator files read {len(tex)} TeX files "
                    f"({', '.join(tex)}); one companion is of one manuscript"]
        return [f"roadmap locators: {pages} pages under {BLUEPRINT}/{ROADMAP}/, {with_lean} with a "
                f"lean: field, {citing} of them citing a locator file that reads a TeX file "
                f"({backed} such file(s) under {BLUEPRINT}/{SOURCES}/)"]

    # -- config ------------------------------------------------------------
    def validate_config(self) -> None:
        if not self.config.get("repo"):
            raise AdapterError(f"{self.source}: 'repo' is required")
        if not self.globs(DEFAULT_GLOBS):
            raise AdapterError(f"{self.source}: 'glob' is required "
                               "(e.g. glob: blueprint/roadmap/**/*.md)")
        mode = str(self.get("blueprint.locator", "auto"))
        if mode not in {"auto", "number", "label"}:
            raise AdapterError(
                f"{self.source}: blueprint.locator must be auto, number or label, not {mode!r}")

    def blueprint_dir(self) -> str:
        return str(self.get("roadmap.blueprint") or BLUEPRINT).strip("/")

    def source_files(self, repo: Path) -> list[Path]:
        """The one TeX file the locators read: ``roadmap.tex``, else the name they give, at the root."""
        named = survey(repo, self.blueprint_dir())["tex"]
        rel = self.get("roadmap.tex") or (named[0] if len(named) == 1 else None)
        if not rel:
            raise AdapterError(
                f"{self.source}: the locator files under {self.blueprint_dir()}/{SOURCES}/ read "
                f"{len(named)} TeX files ({', '.join(named) or 'none'}), and a companion is of one",
                hint="name it in adapter.yaml as roadmap: {tex: <path in the repo>}")
        pure = PurePosixPath(str(rel))
        path = Path(repo).joinpath(*pure.parts)
        if pure.is_absolute() or ".." in pure.parts or not path.is_file():
            raise AdapterError(f"{self.source}: the locators read {rel}, which is not a file of {repo}",
                               hint="name the manuscript in adapter.yaml as roadmap: {tex: <path in the repo>}")
        return [path]

    def roadmap_files(self, repo: Path) -> list[Path]:
        root = Path(repo) / self.blueprint_dir()
        return sorted(p for p in list((root / SOURCES).glob("*.md")) + list((root / ROADMAP).rglob("*.md"))
                      if p.is_file())

    # -- discovery ---------------------------------------------------------
    def manuscript_claim(self, repo: str | Path | None = None):
        """The manuscript as `blueprint_tex` claims one, named for what it is here."""
        claim = super().manuscript_claim(repo)
        if claim is None:
            return None
        tex = self.source_files(self.resolve_repo(repo))[0]
        title = manuscript_title(tex.read_text(encoding="utf-8", errors="ignore"))
        return dataclasses.replace(claim, edition="manuscript", title=title or claim.title)

    # -- parsing -----------------------------------------------------------
    def _statements(self, repo: Path) -> tuple[list[dict], dict, list[dict]]:
        r"""The manuscript's statements, each with the declarations of the pages that cite it.

        The statements, their counters and their text are `blueprint_tex`'s
        reading of the TeX file.  A ``\lean{}`` in it is not this convention's
        and is dropped; the declarations come from the roadmap.
        """
        reading, texplace = _reading()
        statements, declarations, rejects = super()._statements(repo)
        tex = self.source_files(repo)[0]
        text = tex.read_text(encoding="utf-8", errors="ignore")
        source = texplace.Source(text)
        appendix = _APPENDIX_RE.search(text)
        appendix_line = text.count("\n", 0, appendix.start()) + 1 if appendix else None
        for s in statements:
            s.update(decls=[], statement_ok=False, proof_ok=None, uses=[])
            if appendix_line is not None and s["line"] > appendix_line:
                s["reset"] = ""                      # lettered: keyed on its label
        found = survey(repo, self.blueprint_dir())
        counts = {"pages": len(found["pages"]), "pages_with_lean": 0, "pages_cited": 0,
                  "pages_not_cited": 0, "passages": 0, "passages_outside_statements": 0}
        proofs: dict[int, list[bool]] = {}
        for page in found["pages"]:
            meta = page["meta"]
            if not page["names"]:
                continue
            counts["pages_with_lean"] += 1
            if not page["sections"]:
                continue
            if meta.get("origin") != ORIGIN_CITED or meta.get("statement") != FORMALIZED:
                counts["pages_not_cited"] += 1
                continue
            counts["pages_cited"] += 1
            names, bad = self._declarations([", ".join(page["names"])])
            for raw in bad:
                rejects.append({"raw": raw, "reason": "not_a_declaration_name", "file": page["rel"], "label": ""})
            for rel, key in page["sections"]:
                _heading, body = found["locators"][rel]["sections"][key]
                spans, _how = reading.section_spans(body, source, found["locators"][rel]["labels_only"])
                if not spans:
                    continue
                counts["passages"] += 1
                hit = [i for i, s in enumerate(statements)
                       if any((a <= s["line"] and s["end_line"] <= b) or (s["line"] <= a and b <= s["end_line"])
                              for a, b in spans)]
                if not hit:
                    counts["passages_outside_statements"] += 1
                for i in hit:
                    s = statements[i]
                    s["decls"] = list(dict.fromkeys(s["decls"] + names))
                    s["statement_ok"] = True
                    proofs.setdefault(i, []).append(meta.get("proof") == FORMALIZED)
        for i, claimed in proofs.items():
            s = statements[i]
            # a definition has nothing to prove; for anything else the roadmap either says
            # the proof is formalized or leaves a gap
            s["proof_ok"] = True if any(claimed) else (None if s["env"] == "definition" else False)
        self._roadmap_counts = counts
        return statements, declarations, rejects

    # -- interface ---------------------------------------------------------
    def dry_run(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> dict:
        """`blueprint_tex`'s counts, and what the roadmap gave them.

        ``pages_cited`` are the pages whose declarations could become rows
        (``origin: cited``, ``statement: formalized``, citing the manuscript);
        ``pages_not_cited`` cite it and state something else; and
        ``passages_outside_statements`` are the cited passages no statement
        environment takes in or holds, whose pages' declarations are rows of
        nothing.
        """
        report = super().dry_run(repo, sources=sources)
        report.update(self._roadmap_counts)
        report["roadmap_files"] = len(self.roadmap_files(self.resolve_repo(repo)))
        return report

    def key_inputs(self) -> dict:
        inputs = super().key_inputs()
        repo = self.resolve_repo()
        files = self.roadmap_files(repo)
        inputs["roadmap"] = len(files)
        inputs["roadmap_sha"] = sha_of_files(files, repo)
        return inputs
