r"""Adapter: a leanblueprint manuscript's ``\lean{}`` / ``\leanok`` macros.

New code.  The two shipped adapters have to INFER which declaration formalises
which item -- from a docstring tag, or from a curated overlay table.  A
leanblueprint project writes the map down::

    \begin{theorem}\label{thm:x}
      \lean{Foo.bar, Foo.baz}
      \uses{def:y}
      \leanok
      ... the statement, in prose and LaTeX ...
    \end{theorem}

so ``\lean{}`` gives the declarations, ``\label{}`` the locator, ``\uses{}`` the
dependencies the project claims, ``\leanok`` its own status claim, and the
environment body is the manuscript text the quote gate needs -- with no PDF,
because the blueprint .tex IS the manuscript.

Two things about the shape of this convention decide the code:

* **The item is a LaTeX counter, not a dotted book number.**  ``\begin{lemma}``
  numbers itself through ``\newtheorem``, so the locator is recoverable only
  when the preamble's declarations and the file order are both readable.  When
  they are, items are ``Lemma 2.3`` and go through the strict grammar like any
  other item; when they are not, the ``\label`` is the item and the adapter says
  so (``locator`` in :meth:`dry_run`, ``blueprint.locator`` in adapter.yaml).
  Guessing a number that LaTeX would have printed differently is worse than
  keying on the label the repo wrote.
* **The manuscript is self-authored.**  A blueprint has no independent edition,
  so this is reach rather than a missing case of the book-companion model: the
  findings it supports are statement-versus-manuscript disagreement and a stale
  ``\leanok``, never a page anchor or an edition drift.  Rows therefore carry
  ``anchor_source: none``.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterator, Mapping

from cflib import items as cf_items

from .api import Adapter, AdapterError, register, sha_of_files
from .base import make_row

__all__ = ["BlueprintTexAdapter", "DEFAULT_ENVIRONMENTS", "DEFAULT_GLOBS"]

#: The environments that STATE something.  ``proof`` is deliberately absent: it
#: carries its own ``\leanok`` about the proof, which is read as the status of
#: the statement before it, not as an item of its own.
DEFAULT_ENVIRONMENTS: tuple[str, ...] = (
    "theorem", "lemma", "definition", "proposition", "corollary",
)
DEFAULT_GLOBS = ("blueprint/**/*.tex",)

#: Lean keyword implied by the environment.  This is the BLUEPRINT's claim about
#: the shape of the declaration, not a reading of the Lean file -- the adapter
#: never opens one -- and a mismatch between the two is a finding, not a bug.
ENV_DECL_KIND = {"definition": "def"}
DEFAULT_DECL_KIND = "theorem"

_NEWTHEOREM_RE = re.compile(
    r"\\newtheorem\*?\s*\{(?P<env>[A-Za-z]+)\}\s*"
    r"(?:\[(?P<shared>[A-Za-z]+)\]\s*)?"
    r"\{(?P<display>[^}]*)\}"
    r"(?:\s*\[(?P<reset>[A-Za-z]+)\])?")
_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
_CHAPTER_RE = re.compile(r"\\chapter\*?\s*\{")
_SECTION_RE = re.compile(r"\\section\*?\s*\{")
_LABEL_RE = re.compile(r"\\label\s*\{([^}]*)\}")
_LEAN_RE = re.compile(r"\\lean\s*\{([^}]*)\}")
_USES_RE = re.compile(r"\\uses\s*\{([^}]*)\}")
_LEANOK_RE = re.compile(r"\\leanok\b")
#: Every macro whose text is machinery rather than manuscript.  ``leanok`` comes
#: before ``lean`` in the alternation deliberately: ordered alternation would
#: otherwise strip ``\lean`` out of ``\leanok`` and leave the word "ok" standing
#: in the middle of the statement the quote gate is going to quote.
_STRIP_RE = re.compile(
    r"\\(?:leanok|label|lean|uses|proves|discussion)(?![A-Za-z])\s*(?:\{[^}]*\})?")
_OPT_TITLE_RE = re.compile(r"\A\s*\[[^\]]*\]")
_DECL_OK_RE = re.compile(r"^[^\s{}\\,]+$")


def _env_block_re(environments) -> re.Pattern[str]:
    """``\\begin{env} ... \\end{env}`` for the statement environments, non-greedy."""
    alt = "|".join(re.escape(e) for e in environments)
    return re.compile(r"\\begin\s*\{(?P<env>" + alt + r")\}(?P<body>.*?)\\end\s*\{(?P=env)\}", re.S)


def newtheorems(text: str) -> dict[str, dict]:
    r"""``{env: {display, counter, reset}}`` from the preamble's ``\newtheorem``s.

    ``\newtheorem{theorem}{Theorem}[chapter]`` declares a counter reset by
    chapter; ``\newtheorem{lemma}[theorem]{Lemma}`` shares it.  Both spellings
    are the standard ones a leanblueprint preamble uses, and between them they
    are what makes the printed number recoverable at all.
    """
    out: dict[str, dict] = {}
    for m in _NEWTHEOREM_RE.finditer(text or ""):
        env = m.group("env")
        out[env] = {
            "display": (m.group("display") or env.title()).strip(),
            "counter": m.group("shared") or env,
            "reset": (m.group("reset") or "").strip(),
        }
    for body in out.values():
        shared = out.get(body["counter"])
        if shared is not None and shared is not body:
            body["reset"] = shared["reset"]
    return out


def order_files(files, root: Path) -> list[Path]:
    r"""The .tex files in the order LaTeX reads them.

    A blueprint's master file (``content.tex``) lists its chapters with
    ``\input{chapters/...}``; the counter that numbers the items runs through
    them in that order, so reading the files in path order would number them
    wrongly.  Files the master does not name keep path order after the ones it
    does, and a tree with no master is simply path order.
    """
    by_path = {p.resolve(): p for p in files}
    best: list[Path] = []
    for candidate in sorted(files):
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:                                   # pragma: no cover
            continue
        named: list[Path] = []
        for raw in _INPUT_RE.findall(text):
            name = raw.strip()
            for spelling in (name, name + ".tex"):
                target = (candidate.parent / spelling).resolve()
                if target in by_path and by_path[target] not in named:
                    named.append(by_path[target])
                    break
        if len(named) > len(best):
            best = named
    rest = [p for p in sorted(files) if p not in best]
    return best + rest


@register
class BlueprintTexAdapter(Adapter):
    r"""Companions whose item -> declaration map is a leanblueprint manuscript."""

    kind = "blueprint_tex"

    #: A blueprint project names its own manuscript rather than a published
    #: book, so the signals that matter are the repo's own prose and files.
    PRIMARY_SIGNALS = ("prose", "filesystem")

    # -- detection (design S4.2) -------------------------------------------
    #: score = BASE + SPAN * (statement environments carrying \lean / all of
    #: them).  A blueprint whose every statement is mapped scores 0.95; one that
    #: is mostly unmapped scores low on purpose, because there is little for an
    #: audit to read.
    DETECT_BASE_SCORE = 0.6
    DETECT_SPAN = 0.35
    DETECT_MAX_SCORE = 0.95

    @classmethod
    def _measure(cls, repo: str | Path) -> tuple[int, int, int, int]:
        r"""``(tex files, statement envs, envs with \lean, \lean macros)``."""
        from cflib.discovery import tex_files

        files = tex_files(Path(repo))
        block_re = _env_block_re(DEFAULT_ENVIRONMENTS)
        envs = with_lean = macros = 0
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:                               # pragma: no cover
                continue
            macros += len(_LEAN_RE.findall(text))
            for m in block_re.finditer(text):
                envs += 1
                if _LEAN_RE.search(m.group("body")):
                    with_lean += 1
        return len(files), envs, with_lean, macros

    @classmethod
    def detect(cls, repo: str | Path) -> tuple[float, dict]:
        files, envs, with_lean, macros = cls._measure(repo)
        if not files or not with_lean:
            return 0.0, {}
        score = min(cls.DETECT_MAX_SCORE,
                    cls.DETECT_BASE_SCORE + cls.DETECT_SPAN * (with_lean / envs if envs else 0.0))
        return round(score, 4), {
            "reason": f"{with_lean} of {envs} statement environments in {files} "
                      f"blueprint/**/*.tex files carry \\lean{{}} ({macros} macros)",
            "tex_files": files, "environments": envs,
            "with_lean": with_lean, "lean_macros": macros,
        }

    @classmethod
    def explain(cls, repo: str | Path) -> list[str]:
        files, envs, with_lean, macros = cls._measure(repo)
        if not files:
            return ["blueprint tex: no blueprint/**/*.tex in this repo"]
        return [f"blueprint tex: {files} blueprint/**/*.tex, {envs} statement environments, "
                f"{with_lean} with \\lean{{}} ({macros} macros)"]

    # -- config ------------------------------------------------------------
    def validate_config(self) -> None:
        if not self.config.get("repo"):
            raise AdapterError(f"{self.source}: 'repo' is required")
        if not self.globs(DEFAULT_GLOBS):
            raise AdapterError(f"{self.source}: 'glob' is required "
                               "(e.g. glob: blueprint/**/*.tex)")
        mode = str(self.get("blueprint.locator", "auto"))
        if mode not in {"auto", "number", "label"}:
            raise AdapterError(
                f"{self.source}: blueprint.locator must be auto, number or label, not {mode!r}")

    def environments(self) -> tuple[str, ...]:
        raw = self.get("blueprint.environments")
        return tuple(str(e) for e in raw) if raw else DEFAULT_ENVIRONMENTS

    def source_files(self, repo: Path) -> list[Path]:
        return self.matching_files(repo, DEFAULT_GLOBS)

    # -- parsing -----------------------------------------------------------
    def _statements(self, repo: Path) -> tuple[list[dict], dict, list[dict]]:
        r"""Every statement environment in reading order, plus the counter table.

        Returns ``(statements, counters, rejects)``.  A statement is a dict of
        what the .tex says about it -- environment, label, declarations, uses,
        both ``\leanok`` flags, the body text and where it was found -- with no
        item decided yet: the locator needs the whole document before it can say
        whether the numbering is recoverable.
        """
        files = order_files(self.source_files(repo), repo)
        declarations = {}
        texts: dict[Path, str] = {}
        for path in files:
            try:
                texts[path] = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:                               # pragma: no cover
                texts[path] = ""
            declarations.update(newtheorems(texts[path]))
        block_re = _env_block_re(self.environments())
        out: list[dict] = []
        rejects: list[dict] = []
        counters: Counter[str] = Counter()
        chapter = section = 0
        for path in files:
            text = texts[path]
            rel = str(path.relative_to(repo))
            #: a structural command and a statement are ordered by position in
            #: the file, so both streams are walked together
            events = [(m.start(), "chapter", None) for m in _CHAPTER_RE.finditer(text)]
            events += [(m.start(), "section", None) for m in _SECTION_RE.finditer(text)]
            events += [(m.start(), "statement", m) for m in block_re.finditer(text)]
            for _pos, what, m in sorted(events, key=lambda e: e[0]):
                if what == "chapter":
                    chapter += 1
                    section = 0
                    counters.clear()
                    continue
                if what == "section":
                    section += 1
                    continue
                env = m.group("env")
                body = m.group("body")
                declared = declarations.get(env) or {}
                counter = declared.get("counter") or env
                counters[counter] += 1
                label = (_LABEL_RE.search(body).group(1).strip()
                         if _LABEL_RE.search(body) else "")
                decls, bad = self._declarations(_LEAN_RE.findall(body))
                for raw in bad:
                    rejects.append({"raw": raw, "reason": "not_a_declaration_name",
                                    "file": rel, "label": label})
                out.append({
                    "env": env,
                    "display": declared.get("display") or env.title(),
                    "counter": counter,
                    "reset": declared.get("reset", ""),
                    "declared": bool(declared),
                    "chapter": chapter, "section": section, "n": counters[counter],
                    "label": label,
                    "decls": decls,
                    "uses": [u.strip() for part in _USES_RE.findall(body)
                             for u in part.split(",") if u.strip()],
                    "statement_ok": bool(_LEANOK_RE.search(body)),
                    "proof_ok": self._proof_is_ok(text, m.end()),
                    "text": self._statement_text(body),
                    "file": rel,
                    "line": text.count("\n", 0, m.start()) + 1,
                })
        return out, declarations, rejects

    @staticmethod
    def _declarations(parts) -> tuple[list[str], list[str]]:
        r"""``\lean{A.b, C.d}`` split into usable names and unusable ones."""
        good: list[str] = []
        bad: list[str] = []
        for part in parts:
            for raw in str(part).split(","):
                name = raw.strip()
                if not name:
                    continue
                (good if _DECL_OK_RE.match(name) else bad).append(name)
        return list(dict.fromkeys(good)), bad

    @staticmethod
    def _proof_is_ok(text: str, end: int) -> bool | None:
        r"""``\leanok`` of the ``proof`` environment following a statement.

        ``None`` when the statement has no proof environment -- a definition has
        nothing to prove, and calling that an unfinished proof would report every
        definition in the manuscript as a gap.
        """
        m = re.compile(r"\\begin\s*\{([A-Za-z*]+)\}").search(text, end)
        if m is None or m.group(1) != "proof":
            return None
        closing = re.compile(r"\\end\s*\{proof\}").search(text, m.end())
        return bool(_LEANOK_RE.search(text[m.end():closing.start() if closing else len(text)]))

    @staticmethod
    def _statement_text(body: str) -> str:
        """The environment body as manuscript: machinery stripped, space collapsed."""
        text = _OPT_TITLE_RE.sub("", body)
        text = _STRIP_RE.sub(" ", text)
        text = re.sub(r"%.*", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    # -- the locator -------------------------------------------------------
    def _locator(self, statements, kinds) -> str:
        """``number`` when LaTeX's own numbering is recoverable, else ``label``.

        The two are not mixed.  Half a table keyed on ``Lemma 2.3`` and half on
        ``lem:foo`` is two item namespaces pretending to be one, and every later
        stage joins on that key.
        """
        declared = str(self.get("blueprint.locator", "auto"))
        if declared in ("number", "label"):
            return declared
        emitted = [s for s in statements if s["decls"]]
        if not emitted:
            return "label"
        for s in emitted:
            if not s["declared"] or not self._number(s):
                return "label"
            item, _reject = cf_items.try_parse(f"{s['display']} {self._number(s)}", kinds)
            if item is None:
                return "label"
        return "number"

    @staticmethod
    def _number(s: Mapping) -> str:
        """The dotted number LaTeX would print, or ``""`` when it is not dotted.

        A counter reset by nothing prints a bare ``7``, which the item grammar
        refuses (and rightly: it names no chapter), so such a blueprint is keyed
        on its labels instead.
        """
        if s["reset"] == "chapter" and s["chapter"]:
            return f"{s['chapter']}.{s['n']}"
        if s["reset"] == "section" and s["section"]:
            return (f"{s['chapter']}.{s['section']}.{s['n']}" if s["chapter"]
                    else f"{s['section']}.{s['n']}")
        return ""

    def _item_of(self, s: Mapping, locator: str) -> tuple[str, str]:
        """``(item id, the raw locator the repo wrote)``."""
        if locator == "number":
            return f"{s['display']} {self._number(s)}", s["label"] or s["env"]
        return (s["label"] or f"{s['file']}:{s['line']}"), s["label"] or s["env"]

    # -- rows --------------------------------------------------------------
    def _rows(self, repo: Path, binding):
        statements, declarations, rejects = self._statements(repo)
        kinds = self.item_kinds(binding.ids()[0] if binding.ids() else None)
        locator = self._locator(statements, kinds)
        granularity = str(self.config.get("granularity") or "item")

        counts: Counter[str] = Counter()
        counts["files"] = len(self.source_files(repo))
        counts["files_with_statements"] = len({s["file"] for s in statements})
        rows: list[dict] = []
        for s in statements:
            counts["declarations"] += 1
            if s["statement_ok"]:
                counts["statement_leanok"] += 1
            if s["proof_ok"]:
                counts["proof_leanok"] += 1
            counts["uses"] += len(s["uses"])
            if not s["decls"]:
                counts["untagged"] += 1
                continue
            counts["tagged"] += 1
            item, raw = self._item_of(s, locator)
            source_id, source_sha = binding.resolve(file=s["file"], tag=item,
                                                    decl=s["decls"][0])
            # The blueprint's own status claim: done means the statement is
            # marked \leanok AND, when there is a proof to do, so is the proof.
            # A stale \leanok is a finding this adapter exists to expose, so the
            # claim is recorded as written and never checked against the Lean --
            # which this adapter does not read.
            done = s["statement_ok"] and (s["proof_ok"] is not False)
            if not s["text"]:
                counts["no_statement_text"] += 1
            for decl in s["decls"]:
                rows.append(make_row(
                    item_raw=raw, item=item, sub="", item_kind=s["display"],
                    source_id=source_id, decl=decl,
                    kind=ENV_DECL_KIND.get(s["env"], DEFAULT_DECL_KIND),
                    namespace="", file=s["file"], line=s["line"],
                    statement=s["text"] or f"\\begin{{{s['env']}}} {raw}",
                    body=None, sorry=not done, sorry_class="none" if done else "gap",
                    page_anchor=None, page_anchor_raw=None, anchor_source="none",
                    repo=self.name, key=True, anonymous=False, private=False,
                    granularity=granularity, source_sha=source_sha))
        counts["rows"] = len(rows)
        counts["items"] = len({(r["source_id"], r["item"]) for r in rows})
        return rows, counts, rejects, locator, declarations

    # -- interface ---------------------------------------------------------
    def extract(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> Iterator[dict]:
        rows, *_rest = self._rows(self.resolve_repo(repo), self.bind(sources))
        yield from rows

    def dry_run(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> dict:
        path = self.resolve_repo(repo)
        binding = self.bind(sources)
        rows, counts, rejects, locator, declarations = self._rows(path, binding)
        return {
            "adapter": self.kind,
            "name": self.name,
            "repo": str(path),
            "commit": self.repo_commit(path),
            "files": counts["files"],
            "files_with_statements": counts["files_with_statements"],
            "declarations": counts["declarations"],
            "tagged": counts["tagged"],
            "untagged": counts["untagged"],
            "rows": counts["rows"],
            "items": counts["items"],
            # WHICH locator, and why it could be that one: a table keyed on
            # labels and a table keyed on numbers join on nothing, so the reader
            # of statements.jsonl has to be told which this is.
            "locator": locator,
            "newtheorem_declarations": {env: body["display"]
                                        for env, body in sorted(declarations.items())},
            "statement_leanok": counts["statement_leanok"],
            "proof_leanok": counts["proof_leanok"],
            # `\uses{}` is the dependency edge the blueprint draws. Row schema v2
            # has nowhere to carry it, so it is counted here rather than dropped
            # in silence; a schema that can hold it is a separate change.
            "uses_edges": counts["uses"],
            "no_statement_text": counts["no_statement_text"],
            "sorry": sum(1 for r in rows if r["sorry"]),
            "sorry_gap": sum(1 for r in rows if r["sorry_class"] == "gap"),
            "item_kinds": dict(sorted(Counter(r["item_kind"] for r in rows).items())),
            "rejected": len(rejects),
            "rejects": rejects[:50],
            "samples": rows[:3],
            "sources": self.source_report(path),
            "rows_by_source": {sid: sum(1 for r in rows if r["source_id"] == sid)
                               for sid in binding.ids()},
        }

    def key_inputs(self) -> dict:
        inputs = super().key_inputs()
        repo = self.resolve_repo()
        files = self.source_files(repo)
        inputs["sources"] = len(files)
        inputs["sources_sha"] = sha_of_files(files, repo)
        return inputs
