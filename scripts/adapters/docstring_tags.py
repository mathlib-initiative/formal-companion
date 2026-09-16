"""Adapter: declarations tagged by a book label in their docstring.

Ported from
  scratchpad/recovered/b174aa13_L5547_Bash.txt  ``extract_ours.py``   (tao-measure-lean)
  scratchpad/recovered/b174aa13_L5564_Bash.txt  ``extract_teorth.py`` (teorth/analysis)

Both originals were one-off cells; this adapter is the generalisation
design/stage_map.md Stage 2 asks for, with their recorded defects fixed:

1. ONE parser for every repo, handling ``private`` / ``protected`` /
   ``noncomputable`` / ``@[...]`` prefixes.  ``extract_ours``'s prefix list
   missed ``noncomputable instance`` and every ``private noncomputable`` form,
   and the three parsers on disk disagreed (1,248 / 1,414 / 1,413
   declarations).  See ``adapters.leanparse``.
2. Item kinds come from campaign.yaml, not from a regex baked into the
   extractor, and every tag goes through the strict grammar in ``cflib.items``
   so junk (``Corollary (Lemma 1.2.5 + Exercise 1.2.4)``) is reported, not
   turned into an item id.
3. The docstring's OWN page anchor beats a cross-reference: Exercise 1.2.17
   took its anchor from "previewed on book p. 20" (``adapters.itemtag``).
4. ``def`` / ``abbrev`` / ``instance`` bodies are captured: the
   ``elementaryMeasure := volume`` proxy was found by reading a body, not a
   signature (adam.NOTES.md 16-19).
5. Anonymous ``example``s get the synthetic id ``example@<file>:<line>`` so
   they are attributable; Exercise 1.6.37 was dispatched as SINGLE-SOURCE
   although it had been formalised anonymously.

``sorry`` is classified rather than reported: ``gap`` for the repo's gap
marker (``sorry -- TODO(autoform)``), ``exercise`` for a bare sorry left for
the reader, ``none`` otherwise (make_verso_tables.py:236-244).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterator

from cflib import items as cf_items

from . import leanparse
from .api import Adapter, AdapterError, register, sha_of_files
from .base import make_row
from .itemtag import find_page_anchor, find_tag

#: tao-measure-lean's in-progress marker; `sorry` under it is a gap, not an
#: exercise left for the reader (make_verso_tables.py TODO_SORRY_RE).
DEFAULT_GAP_MARKER = r"\bsorry\b\s*--\s*TODO"
BARE_SORRY = re.compile(r"\bsorry\b")
DEFAULT_GLOBS = ("**/*.lean",)


@register
class DocstringTagsAdapter(Adapter):
    """Companions whose declarations carry a book tag in their docstring."""

    kind = "docstring_tags"

    # -- config ------------------------------------------------------------
    def validate_config(self) -> None:
        if not self.config.get("repo"):
            raise AdapterError(f"{self.source}: 'repo' is required")
        if not self.globs(DEFAULT_GLOBS):
            raise AdapterError(
                f"{self.source}: 'glob' is required "
                "(e.g. glob: TaoMeasure/Section_*.lean)")

    def decl_kinds(self) -> tuple[str, ...]:
        kinds = self.config.get("decl_kinds")
        return tuple(str(k) for k in kinds) if kinds else leanparse.DEFAULT_DECL_KINDS

    def body_kinds(self) -> frozenset[str]:
        kinds = self.config.get("body_for_kinds")
        return frozenset(str(k) for k in kinds) if kinds else leanparse.DEFINITION_KINDS

    def gap_marker(self) -> re.Pattern[str]:
        """The in-progress marker, from a literal adapter.yaml value.

        ``gap_marker: "sorry -- TODO"`` is a literal, not a regex: the tokens
        are escaped and joined by a whitespace pattern so the marker matches
        however the repo spaces it, and ``sorry -- TODO(autoform)`` matches on
        its prefix.
        """
        raw = str(self.get("sorry.gap_marker", "") or "").strip()
        if not raw:
            return re.compile(DEFAULT_GAP_MARKER)
        tokens = [re.escape(t) for t in raw.split()]
        try:
            return re.compile(r"\s+".join(tokens))
        except re.error as exc:  # pragma: no cover - bad config
            raise AdapterError(f"{self.source}: sorry.gap_marker is unusable: {exc}")

    def exercise_sorry_is_permanent(self) -> bool:
        """``sorry.exercise_sorry: permanent`` -- a bare sorry is an exercise.

        When a repo declares ``none``, every bare sorry is an unfinished gap
        instead, so it is never silently accepted as "left for the reader".
        """
        return str(self.get("sorry.exercise_sorry", "permanent")).lower() != "none"

    def page_pattern(self) -> re.Pattern[str] | None:
        raw = self.get("tag.page_pattern")
        if not raw:
            return None
        try:
            return re.compile(str(raw), re.I)
        except re.error as exc:  # pragma: no cover - bad config
            raise AdapterError(f"{self.source}: tag.page_pattern is not a valid regex: {exc}")

    def tag_pattern(self) -> re.Pattern[str] | None:
        raw = self.get("tag.pattern")
        if not raw:
            return None
        try:
            return re.compile(str(raw))
        except re.error as exc:  # pragma: no cover - bad config
            raise AdapterError(f"{self.source}: tag.pattern is not a valid regex: {exc}")

    def source_files(self, repo: Path) -> list[Path]:
        return self.matching_files(repo, DEFAULT_GLOBS)

    # -- parsing -----------------------------------------------------------
    def _tag_of(self, doc: str | None):
        if not doc:
            return None
        # position: docstring_start (the tag opens the docstring, with a
        # backticked tag near the start as the documented fallback) or
        # docstring_anywhere (scan the whole docstring, extract_ours's rule).
        # The LEADING tag always wins; `inline_window` only bounds the
        # fallback used when the docstring does not open with a tag (0 = no
        # limit, which is what extract_ours.py did and what five declarations
        # in tao-measure-lean need -- their tag sits past the first sentence).
        anywhere = str(self.get("tag.position", "docstring_start")) == "docstring_anywhere"
        return find_tag(
            doc,
            keywords=self.item_kinds(),
            require_backticks=bool(self.get("tag.require_backticks", True)),
            inline_window=0 if anywhere else int(self.get("tag.inline_window", 0)),
            pattern=self.tag_pattern(),
        )

    def _parse_item(self, tag_text: str):
        """(item id, sub, book kind, reject reason) via the strict grammar."""
        item, reject = cf_items.try_parse(tag_text, self.numbered_kinds())
        if item is None:
            return None, "", None, reject.reason
        return item.id, item.sub, item.kind, None

    def _classify_sorry(self, body: str, gap_re: re.Pattern[str],
                        exercise_permanent: bool = True) -> tuple[bool, str]:
        """(has sorry, class): gap marker wins, then the repo's sorry policy."""
        stripped = leanparse.strip_comments(body)
        if gap_re.search(body):
            return True, "gap"
        if BARE_SORRY.search(stripped):
            return True, "exercise" if exercise_permanent else "gap"
        return False, "none"

    def _rows(self, repo: Path, book_sha: str | None):
        decl_kinds = self.decl_kinds()
        body_kinds = self.body_kinds()
        gap_re = self.gap_marker()
        exercise_permanent = self.exercise_sorry_is_permanent()
        page_re = self.page_pattern()
        max_body = int(self.get("max_body_lines", 40))
        key_flag = str(self.get("key_flag", "all"))
        clause_window = int(self.get("tag.clause_window", 140))
        granularity = str(self.config.get("granularity") or "item")

        counts: Counter[str] = Counter()
        rejects: list[dict] = []
        rows: list[dict] = []
        seen_items: set[str] = set()

        for path in self.source_files(repo):
            rel = str(path.relative_to(repo))
            counts["files"] += 1
            for decl in leanparse.parse_file(path, rel=rel, decl_kinds=decl_kinds,
                                             max_body_lines=max_body):
                counts["declarations"] += 1
                if decl.private:
                    counts["private"] += 1
                if decl.anonymous:
                    counts["anonymous_declarations"] += 1
                tag = self._tag_of(decl.doc)
                if tag is None:
                    counts["untagged"] += 1
                    continue
                counts["tagged"] += 1
                item_id, sub, item_kind, reason = self._parse_item(tag.tag)
                if item_id is None:
                    counts["rejected"] += 1
                    rejects.append({"raw": tag.tag, "reason": reason, "decl": decl.full_name,
                                    "file": rel, "line": decl.line})
                    continue
                anchor = find_page_anchor(decl.doc, tag, clause_window=clause_window,
                                          page_re=page_re)
                counts[f"anchor_{anchor.source}"] += 1
                has_sorry, sorry_class = self._classify_sorry(decl.body, gap_re,
                                                              exercise_permanent)
                if has_sorry:
                    counts["sorry"] += 1
                    counts[f"sorry_{sorry_class}"] += 1
                is_key = key_flag == "all" or (key_flag == "first" and item_id not in seen_items)
                seen_items.add(item_id)
                rows.append(make_row(
                    item_raw=tag.tag,
                    item=item_id,
                    sub=sub,
                    item_kind=item_kind,
                    decl=decl.full_name,
                    kind=decl.kind,
                    namespace=decl.namespace,
                    file=rel,
                    line=decl.line,
                    statement=leanparse.signature(decl.body),
                    body=decl.def_body if decl.kind in body_kinds else None,
                    sorry=has_sorry,
                    sorry_class=sorry_class,
                    page_anchor=anchor.page,
                    page_anchor_raw=anchor.raw,
                    anchor_source=anchor.source,
                    repo=self.name,
                    key=bool(is_key),
                    anonymous=decl.anonymous,
                    private=decl.private,
                    granularity=granularity,
                    book_sha=self.book_sha(book_sha),
                ))
        counts["rows"] = len(rows)
        counts["items"] = len({r["item"] for r in rows})
        return rows, counts, rejects

    # -- interface ---------------------------------------------------------
    def extract(self, repo: str | Path | None = None, book_sha: str | None = None) -> Iterator[dict]:
        rows, _counts, _rejects = self._rows(self.resolve_repo(repo), book_sha)
        yield from rows

    def dry_run(self, repo: str | Path | None = None) -> dict:
        path = self.resolve_repo(repo)
        rows, counts, rejects = self._rows(path, None)
        return {
            "adapter": self.kind,
            "name": self.name,
            "repo": str(path),
            "commit": self.repo_commit(path),
            "files": counts["files"],
            "declarations": counts["declarations"],
            "tagged": counts["tagged"],
            "untagged": counts["untagged"],
            "private": counts["private"],
            "anonymous": sum(1 for r in rows if r["anonymous"]),
            "anonymous_declarations": counts["anonymous_declarations"],
            "rows": counts["rows"],
            "items": counts["items"],
            "sorry": counts["sorry"],
            "sorry_exercise": counts["sorry_exercise"],
            "sorry_gap": counts["sorry_gap"],
            "anchors": {"own": counts["anchor_own"], "crossref": counts["anchor_crossref"],
                        "none": counts["anchor_none"]},
            "item_kinds": dict(sorted(Counter(r["item_kind"] for r in rows).items())),
            "rejected": counts["rejected"],
            "rejects": rejects[:50],
            "samples": rows[:3],
        }

    def key_inputs(self) -> dict:
        inputs = super().key_inputs()
        repo = self.resolve_repo()
        files = self.source_files(repo)
        inputs["sources"] = len(files)
        inputs["sources_sha"] = sha_of_files(files, repo)
        return inputs
