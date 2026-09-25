"""Adapter: declarations tagged by a book label in their docstring.

Ported from the v1 campaign's two extraction scripts (not in this repo):
``extract_ours.py`` (tao-measure-lean) and ``extract_teorth.py``
(teorth/analysis).

Both originals were one-off cells; this adapter is the generalisation
docs/design/stage_map.md Stage 2 asks for, with their recorded defects fixed:

1. ONE parser for every repo, handling ``private`` / ``protected`` /
   ``noncomputable`` / ``@[...]`` prefixes.  ``extract_ours``'s prefix list
   missed ``noncomputable instance`` and every ``private noncomputable`` form,
   and the three parsers on disk disagreed (1,248 / 1,414 / 1,413
   declarations).  See ``adapters.leanparse``.
2. Item kinds come from the bound source (workspace.yaml), not from a regex
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
from typing import Iterator, Mapping

from cflib import items as cf_items

from . import leanparse
from .api import Adapter, AdapterError, register, sha_of_files
from .base import make_row
from .itemtag import find_page_anchor, find_tag, kinds_from_labels

#: tao-measure-lean's in-progress marker; `sorry` under it is a gap, not an
#: exercise left for the reader (make_verso_tables.py TODO_SORRY_RE).
DEFAULT_GAP_MARKER = r"\bsorry\b\s*--\s*TODO"
BARE_SORRY = re.compile(r"\bsorry\b")
DEFAULT_GLOBS = ("**/*.lean",)

#: The opening of a Lean docstring -- enough of it to hold a tag. Used only by
#: :meth:`DocstringTagsAdapter.observed_kinds`, which reads the repo's grammar
#: before any kind list exists to parse it with.
#:
#: The head is a LOOKAHEAD, not a capture: a consuming ``/--(.{0,120})`` swallows
#: the next docstring whenever two declarations sit within 120 characters of each
#: other, and the kinds only those declarations use then never appear.
DOCSTRING_HEAD_RE = re.compile(r"/--(?=(.{0,120}))", re.S)

# -- detection (design S4.2) -------------------------------------------------
# These constants describe THIS convention and nothing else, so they live beside
# the parser that uses them: adding a convention is adding a file, not editing
# the core's detector as well.

#: Below this many .lean files a tree is not a Lean companion.  Kept as a floor
#: on FILES rather than declarations deliberately: a repo with fewer carries too
#: little evidence either way, and admitting it only lets it score 0 further on.
MIN_LEAN_FILES = 20
#: The score is a sample, not a census: reading every file of a large companion
#: to decide which adapter to use costs more than the decision is worth.
DETECT_FILE_LIMIT = 400
DETECT_SAMPLE = 200
#: score = BASE + SPAN * (tagged / documented), capped.  A repo where every
#: documented file opens with a tag scores 0.95, never 1.0: detection is
#: evidence, and `cf onboard --adapter` is what certainty looks like.
DETECT_BASE_SCORE = 0.6
DETECT_SPAN = 0.35
DETECT_MAX_SCORE = 0.95
#: A bare label has no delimiter, so a docstring that merely OPENS with a prose
#: sentence naming a numbered theorem matches it.  The bare reading is therefore
#: believed only when it is the repo's HABIT: below this fraction of documented
#: files the backticked count stands.  Measured over the Lean repos to hand: the
#: two bare-label companions sit at 0.84 and 0.96, while a blueprint research
#: project with five such docstrings sits at 0.03 -- and without this floor that
#: project was auto-classified as a docstring-tagged companion at 0.61, which is
#: a worse answer than the refusal it used to get.
MIN_BARE_FRACTION = 0.25


def _tag_counts(files, sample: int = DETECT_SAMPLE) -> tuple[int, int, int]:
    """``(backticked, bare, documented)`` over the sampled files.

    Both spellings of the convention are counted, because both are real and the
    repo decides which: tao-measure-lean wraps the label in backticks and
    teorth/analysis writes it bare at the start of the docstring (see
    :func:`adapters.itemtag.find_tag`).  Scoring only the backticked spelling is
    what made teorth/analysis -- the second convention this adapter was written
    for -- undetectable: 0 of its 102 documented files match it and 86 match the
    bare one.

    The bare count is a superset of the backticked one (``find_tag`` prefers a
    backticked leading tag whatever the flag says), so the two are comparable
    directly and equality means "no bare-only tags", i.e. keep the strict
    spelling.

    The denominator counts only files that carry a docstring at all.  A file
    with none is not evidence against the convention: tao-measure-lean has 154
    Lean files of which 112 are generated blueprint stubs with no docstring, and
    counting those would score a repo whose every documented declaration is
    tagged at 0.70 instead of 0.95.
    """
    backticked = bare = documented = 0
    for path in list(files)[:sample]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:                                   # pragma: no cover
            continue
        heads = [m.group(1).strip() for m in DOCSTRING_HEAD_RE.finditer(text)]
        if not heads:
            continue
        documented += 1
        if any(find_tag(head) for head in heads):
            backticked += 1
        if any(find_tag(head, require_backticks=False) for head in heads):
            bare += 1
    return backticked, bare, documented


def _declaration_collisions(rows: list[dict]) -> list[dict]:
    """Declarations that appear in more than one file, with the files.

    Two files defining the same fully qualified name are two modules that are
    never imported together -- a generated mirror of the real ones.  Which is
    the audited copy is not decidable here: picking by path order would happily
    audit a blueprint stub instead of the proof it mirrors.
    """
    by_decl: dict[str, list[dict]] = {}
    for row in rows:
        by_decl.setdefault(row["decl"], []).append(row)
    out = []
    for decl, group in sorted(by_decl.items()):
        files = sorted({r["file"] for r in group})
        if len(files) > 1:
            out.append({"decl": decl, "item": group[0]["item"], "files": files})
    return out


@register
class DocstringTagsAdapter(Adapter):
    """Companions whose declarations carry a book tag in their docstring."""

    kind = "docstring_tags"

    #: The repo signals this convention owns (design S4.3): the
    #: mathlib-initiative ``formalization.yaml`` sources list, and the Autoform
    #: ``graph.json`` metadata a docstring-tagged repo is usually built from.
    #: Ranked first by :meth:`discover_sources`; the rest still run.
    PRIMARY_SIGNALS = ("formalization_yaml", "graph_json")

    # -- detection (design S4.2) -------------------------------------------
    @classmethod
    def _measure(cls, repo: str | Path) -> tuple[list, int, int, int, int]:
        """``(files, sampled, backticked, bare, documented)`` for one repo."""
        from cflib.discovery import lean_files

        files = lean_files(Path(repo), limit=DETECT_FILE_LIMIT)
        sampled = min(len(files), DETECT_SAMPLE)
        backticked, bare, documented = _tag_counts(files, sample=sampled)
        return files, sampled, backticked, bare, documented

    @classmethod
    def detect(cls, repo: str | Path) -> tuple[float, dict]:
        """Score this repo, and decide WHICH SPELLING of the tag it uses.

        The spelling is a fact about the repo, not a default: the winner is
        recorded as ``require_backticks`` in the evidence and `cf onboard`
        writes it into the generated adapter.yaml, so a bare-label companion
        extracts rows instead of finding one backticked tag in 2,371 docstrings.
        Ties keep backticks, which is the stricter reading -- it is what keeps a
        prose mention of "Lemma 1.2.5" out of the table.
        """
        files, sampled, backticked, bare, documented = cls._measure(repo)
        if len(files) < MIN_LEAN_FILES:
            return 0.0, {}
        bare_is_the_habit = (bare > backticked
                             and documented
                             and bare / documented >= MIN_BARE_FRACTION)
        require_backticks = not bare_is_the_habit
        tagged = backticked if require_backticks else bare
        if not tagged:
            return 0.0, {}
        score = min(DETECT_MAX_SCORE,
                    DETECT_BASE_SCORE + DETECT_SPAN * (tagged / documented if documented else 0.0))
        spelling = "backticked" if require_backticks else "bare (teorth/analysis style)"
        return round(score, 4), {
            "reason": f"{tagged} of {documented} sampled .lean files with a docstring open "
                      f"one with a {spelling} book tag "
                      f"({sampled - documented} carry no docstring)",
            "lean_files": len(files), "sampled": sampled,
            "documented": documented, "tagged": tagged,
            "tagged_backticked": backticked, "tagged_bare": bare,
            # written into adapter.yaml by `cf onboard` (tag.require_backticks)
            "require_backticks": require_backticks,
        }

    @classmethod
    def explain(cls, repo: str | Path) -> list[str]:
        files, sampled, backticked, bare, documented = cls._measure(repo)
        if len(files) < MIN_LEAN_FILES:
            return [f"docstring tags: {len(files)} .lean files, "
                    f"fewer than the {MIN_LEAN_FILES} this convention needs"]
        line = (f"docstring tags: {max(backticked, bare)}/{documented} documented files "
                f"({backticked} backticked, {bare} bare)")
        if bare > backticked:
            if documented and bare / documented < MIN_BARE_FRACTION:
                # Below the floor the measurement KEEPS the backticked spelling,
                # so `--adapter docstring_tags` writes require_backticks: true.
                # Saying "set it false in adapter.yaml" without saying that read
                # as a promise the tool would write false, and the user found
                # true in the file it had just generated.
                line += (f" -- {bare / documented:.0%} bare, below the "
                         f"{MIN_BARE_FRACTION:.0%} that reads as a convention: "
                         "`--adapter docstring_tags` writes tag.require_backticks: true, "
                         "edit it to false in the generated adapter.yaml")
            else:
                line += (" -- the bare spelling is the habit here, so "
                         "`--adapter docstring_tags` writes tag.require_backticks: false")
        return [line]

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

    # -- discovery ---------------------------------------------------------
    def observed_kinds(self, repo: str | Path | None = None,
                       min_count: int = 2) -> tuple[str, ...]:
        """The item kinds this repo's docstring tags actually use.

        Read, not declared: every docstring head is matched against
        :data:`adapters.itemtag.TAG_KIND_RE`, so a companion that writes
        ``Problem 2.3.1`` reports ``Problem`` and one that writes
        ``Exercise 1.3.19`` reports ``Exercise``, with no kind list anywhere in
        the loop.  ``min_count`` is the noise floor.
        """
        heads: list[str] = []
        for path in self.source_files(self.resolve_repo(repo)):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:                              # pragma: no cover - unreadable file
                continue
            heads += [m.group(1).strip() for m in DOCSTRING_HEAD_RE.finditer(text)]
        return kinds_from_labels(heads, min_count=min_count)

    # -- parsing -----------------------------------------------------------
    def _tag_keywords(self, binding) -> tuple[str, ...]:
        """The kinds used to FIND a tag: the union over every bound source.

        Finding a tag and parsing it are two steps with two kind lists.  The
        grammar is per source (design S6.1), so the item is parsed against the
        kinds of the source the row is attributed to; but a companion bound to
        two sources must be able to SEE a tag belonging to either before it can
        say which, so the search uses the union.
        """
        out: list[str] = []
        for sid in binding.ids() or [None]:
            for kind in self.item_kinds(sid):
                if kind not in out:
                    out.append(kind)
        return tuple(out)

    def _tag_of(self, doc: str | None, keywords=None):
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
            keywords=keywords or self.item_kinds(),
            require_backticks=bool(self.get("tag.require_backticks", True)),
            inline_window=0 if anywhere else int(self.get("tag.inline_window", 0)),
            pattern=self.tag_pattern(),
        )

    def _parse_item(self, tag_text: str, source_id: str | None = None):
        """(item id, sub, book kind, reject reason) via the strict grammar.

        The kind list is the one belonging to ``source_id``: a book with
        ``Problem 2.3.1`` and a book with ``Exercise 2.3.1`` do not share a
        grammar, and an item is the pair (source_id, item).
        """
        item, reject = cf_items.try_parse(tag_text, self.numbered_kinds(source_id))
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

    def _rows(self, repo: Path, binding):
        decl_kinds = self.decl_kinds()
        body_kinds = self.body_kinds()
        gap_re = self.gap_marker()
        exercise_permanent = self.exercise_sorry_is_permanent()
        page_re = self.page_pattern()
        max_body = int(self.get("max_body_lines", 40))
        key_flag = str(self.get("key_flag", "all"))
        clause_window = int(self.get("tag.clause_window", 140))
        granularity = str(self.config.get("granularity") or "item")

        keywords = self._tag_keywords(binding)

        counts: Counter[str] = Counter()
        rejects: list[dict] = []
        rows: list[dict] = []
        seen_items: set[tuple[str, str]] = set()

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
                tag = self._tag_of(decl.doc, keywords)
                if tag is None:
                    counts["untagged"] += 1
                    continue
                counts["tagged"] += 1
                # WHICH source before WHICH item: the tag may name another
                # source (sources.map tag_prefix), and the item grammar that
                # parses it belongs to that source.
                source_id, source_sha = binding.resolve(file=rel, tag=tag.tag,
                                                        decl=decl.full_name)
                item_id, sub, item_kind, reason = self._parse_item(tag.tag, source_id)
                if item_id is None:
                    counts["rejected"] += 1
                    rejects.append({"raw": tag.tag, "reason": reason, "decl": decl.full_name,
                                    "file": rel, "line": decl.line, "source_id": source_id})
                    continue
                anchor = find_page_anchor(decl.doc, tag, clause_window=clause_window,
                                          page_re=page_re)
                counts[f"anchor_{anchor.source}"] += 1
                has_sorry, sorry_class = self._classify_sorry(decl.body, gap_re,
                                                              exercise_permanent)
                if has_sorry:
                    counts["sorry"] += 1
                    counts[f"sorry_{sorry_class}"] += 1
                # "first" is per (source, item): the same label in two sources
                # is two items, and each deserves its own key row.
                is_key = key_flag == "all" or (key_flag == "first"
                                               and (source_id, item_id) not in seen_items)
                seen_items.add((source_id, item_id))
                rows.append(make_row(
                    item_raw=tag.tag,
                    item=item_id,
                    sub=sub,
                    item_kind=item_kind,
                    source_id=source_id,
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
                    source_sha=source_sha,
                    # this convention IS the Lean sources: there is no export
                    # in between that could omit a declaration.
                    provenance="docstring",
                ))
        counts["rows"] = len(rows)
        counts["items"] = len({(r["source_id"], r["item"]) for r in rows})
        return rows, counts, rejects

    # -- interface ---------------------------------------------------------
    def extract(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> Iterator[dict]:
        rows, _counts, _rejects = self._rows(self.resolve_repo(repo), self.bind(sources))
        yield from rows

    def dry_run(self, repo: str | Path | None = None, *,
                sources: "Mapping[str, str] | None" = None) -> dict:
        path = self.resolve_repo(repo)
        binding = self.bind(sources)
        rows, counts, rejects = self._rows(path, binding)
        collisions = _declaration_collisions(rows)
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
            # The same fully qualified declaration read from two files: a
            # generated mirror of the companion's own modules is inside the
            # glob (tao-measure-lean's Blueprint/Gen/S_*.lean mirror
            # TaoMeasure/Section_*.lean). Neither copy can be preferred
            # mechanically -- a stub and a proof look the same from here -- so
            # this is REPORTED, at the stage where narrowing `glob:` is still
            # one edit, rather than dropped or discovered by the schema gate.
            "duplicate_declarations": len(collisions),
            "duplicates": collisions[:20],
            "samples": rows[:3],
            # What this repo says its sources ARE, and which of them the rows
            # were attributed to. `cf onboard` prints both, so a repo that names
            # two editions and tags only one is visible at onboarding rather
            # than three stages later.
            "sources": self.source_report(path),
            # ROWS, not tags: a tag the strict grammar refuses is counted under
            # `rejected`, and reporting it here as an attributed row would make
            # the two numbers disagree by exactly the rejects.
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
