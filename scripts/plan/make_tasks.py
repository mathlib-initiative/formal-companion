"""Generate tend task files for a run, and reconcile the count.

Ported from the task files of the v1 campaign's five entrypoints (its
``tao-diff-run`` and ``tao-attack-hunt`` roots) — the
generator itself was never committed anywhere, which is how the 411-vs-410
discrepancy survived: the differential run shipped 382 tasks and nobody could
show the rule that produced them. ``Exercise    1.3.19`` (multiple spaces in
the book's text layer) was dropped by a single-space regex and its task was
never written.

So this module's contract is reconciliation, not templating:

* a differential run's task set must equal ``items ∩ (any repo)`` — every queue
  entry becomes exactly one task, and a caller may pass the expected item set
  (``items.json`` ∩ statements) to have it checked;
* an attack run's task set must equal the companion's target list;
* an item that is dropped for any reason — unparseable, slug collision,
  duplicate — is an error that names the item, never a silently shorter run.

Every entry, task and reconciliation key carries its ``source_id``, because an
item is the pair ``(source_id, item)`` (docs/design/archive/multi-source-model.md S6.1).
Reconciling on the bare label would make two sources' ``Exercise 1.3.19`` one
item, and the whole contract of this module is that nothing merges silently.

Task shape (``tend.orchestrator.tasks.Task``): ``schema_version, id, title,
status, priority, depends_on, summary, description``. Titles carry the evidence
class (``DIFFERENTIAL:``/``SINGLE-SOURCE:``/``hunt:``/``vac:``) so that a
finding's strength survives into the corpus.
"""

from __future__ import annotations

import collections
import pathlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from cflib import items as cfitems
from cflib.errors import CfError
from cflib.paths import templates_dir

from .presets import Preset, priority_for
from .render import render_file

_BAD_YAML_CHARS = ('"', "\\", "\n", "\r", "\t")

#: tend's ``TaskPriority`` is exactly {default, high, max}. The pair stage's
#: prioritiser also emits low/medium (its own urgency scale), so everything is
#: mapped here and anything unknown is refused rather than written into a task
#: file that ``tend-task verify`` would reject.
TEND_PRIORITIES = ("default", "high", "max")
PRIORITY_ALIASES = {"low": "default", "medium": "default", "normal": "default", "": "default"}


def tend_priority(value: str | None) -> str:
    """Map any planner priority onto tend's three."""
    v = (value or "default").strip().lower()
    v = PRIORITY_ALIASES.get(v, v)
    if v not in TEND_PRIORITIES:
        raise CfError(
            f"priority {value!r} is not a tend priority",
            hint="tend accepts default, high or max; extend PRIORITY_ALIASES to map yours",
        )
    return v


@dataclass
class Entry:
    """One planned task: a source item plus everything the prompt needs."""

    item: str                       # the label as the queue/targets carry it
    repos: list[str]                # repos formalising it (differential)
    decls: dict[str, list[str]]     # repo -> declaration names
    source_id: str = ""             # which source the label belongs to
    printed_page: int | None = None
    section: str | None = None
    single_source: bool = True
    peers: list[str] = None  # type: ignore[assignment]
    priority: str | None = None

    def __post_init__(self) -> None:
        if self.peers is None:
            self.peers = []

    @property
    def qualified(self) -> str:
        """``<source_id>::<item>`` -- the key nothing can collide on."""
        return cfitems.qualified_item(self.source_id, self.item)


@dataclass
class Task:
    """A rendered task file."""

    id: str
    item: str
    kind: str                       # DIFFERENTIAL | SINGLE-SOURCE | hunt | vac
    priority: str
    text: str
    depends_on: list[str]
    source_id: str = ""

    @property
    def qualified(self) -> str:
        return cfitems.qualified_item(self.source_id, self.item)


# --------------------------------------------------------------------------- #
# input normalisation
# --------------------------------------------------------------------------- #
def _page_from(entry: dict, offset: int) -> int | None:
    """Printed page: an explicit field wins; else the first PDF page hint."""
    for key in ("printed_page", "page", "book_page"):
        if entry.get(key) not in (None, ""):
            try:
                return int(entry[key])
            except (TypeError, ValueError):
                return None
    pages = entry.get("pdf_pages") or []
    if pages:
        try:
            return int(pages[0]) - offset
        except (TypeError, ValueError):
            return None
    return None


def _decls_map(entry: dict, repos: Sequence[str], companion: str | None) -> dict[str, list[str]]:
    """Accept ``decls`` as {repo: [...]}, as a flat list, or from pairs rows."""
    raw = entry.get("decls")
    if isinstance(raw, dict):
        return {r: [str(d) for d in (raw.get(r) or [])] for r in repos}
    if isinstance(raw, list):
        target = companion or (repos[0] if repos else "")
        return {target: [str(d) for d in raw]}
    out: dict[str, list[str]] = {}
    for repo in repos:
        rows = entry.get(repo)
        if isinstance(rows, list):
            out[repo] = [str(r.get("decl")) for r in rows if isinstance(r, dict) and r.get("decl")]
    return out


def queue_entries(queue: Iterable[dict], *, source_id: str, offset: int = 0) -> list[Entry]:
    """Normalise ``pairs/<source_id>/queue.json`` rows into planning entries.

    ``offset`` is THAT SOURCE's page offset: the two Tao editions share
    ``pdf = printed + 16`` but a different source will not, and a pdf_pages hint
    converted with the wrong offset points a worker at the wrong page.
    """
    out: list[Entry] = []
    for row in queue:
        repos = [str(r) for r in (row.get("repos") or [])]
        if not repos:
            raise CfError(f"queue entry for {row.get('item')!r} names no repo")
        out.append(
            Entry(
                item=str(row["item"]),
                repos=repos,
                decls=_decls_map(row, repos, None),
                source_id=str(row.get("source_id") or source_id),
                printed_page=_page_from(row, offset),
                section=row.get("section"),
                single_source=len(repos) == 1,
                peers=[r for r in repos[1:]],
            )
        )
    return out


def differential_entries(
    queue: Iterable[dict],
    targets_by_companion: Mapping[str, Iterable[dict]],
    *,
    source_id: str,
    offset: int = 0,
    chapters: Sequence[int] | None = None,
    kinds: Sequence[str] = cfitems.DEFAULT_KINDS,
    require_in_book: bool = True,
) -> list[Entry]:
    """The differential universe: every numbered book item ANY companion formalises.

    ``queue.json`` only carries the items at least two companions formalise
    (that is what ``pairs.jsonl`` means), so the 158 SINGLE-SOURCE tasks of the
    original run come from the per-companion ``targets.<name>.json`` files. This
    function is where "task count == items ∩ any-repo" is actually produced:
    both sources are merged by canonical item id and nothing is dropped
    silently.

    Filters, all of them explicit because each one cost a task in the original
    run: In-text items cannot pair (no shared id), ``chapters`` restricts the
    audited chapter, and an item the book does not contain (``in_book: false``
    — e.g. a repo labelling something ``Definition 1.1.15`` when the book has
    only ``Exercise 1.1.15``) is not a book item at all.
    """
    repos: dict[str, list[str]] = {}
    decls: dict[str, dict[str, list[str]]] = {}
    pages: dict[str, int | None] = {}
    sections: dict[str, str | None] = {}
    in_book: dict[str, bool] = {}

    def _note(item: str, repo: str, row: dict, row_decls: list[str]) -> None:
        repos.setdefault(item, [])
        if repo not in repos[item]:
            repos[item].append(repo)
        bucket = decls.setdefault(item, {}).setdefault(repo, [])
        for d in row_decls:
            if d and d not in bucket:
                bucket.append(str(d))
        if pages.get(item) is None:
            pages[item] = _page_from(row, offset)
        if sections.get(item) is None:
            sec = row.get("section")
            sections[item] = str(sec) if sec is not None else None
        if "in_book" in row:
            in_book[item] = bool(row["in_book"]) and in_book.get(item, True)

    for row in _rows(queue):
        item = str(row["item"])
        row_repos = [str(r) for r in (row.get("repos") or [])]
        mapped = _decls_map(row, row_repos, None)
        for repo in row_repos:
            _note(item, repo, row, mapped.get(repo) or [])
    for companion, rows in targets_by_companion.items():
        for row in _rows(rows):
            item = str(row["item"])
            raw = row.get("decls")
            row_decls = [str(d) for d in raw] if isinstance(raw, list) else list(
                (raw or {}).get(companion, [])
            )
            _note(item, str(companion), row, row_decls)

    out: list[Entry] = []
    for item in sorted(repos, key=lambda i: cfitems.sort_key(i, kinds) if _parses(i, kinds) else (9, 0, 0, 0, (), i)):
        parsed, _ = cfitems.try_parse(item, kinds)
        if parsed is None or not parsed.numbered or parsed.in_text:
            continue
        if not in_chapters(item, chapters, kinds):
            continue
        if require_in_book and in_book.get(item, True) is False:
            continue
        rs = repos[item]
        out.append(
            Entry(
                item=item,
                repos=rs,
                decls=decls.get(item, {}),
                source_id=source_id,
                printed_page=pages.get(item),
                section=sections.get(item),
                single_source=len(rs) == 1,
                peers=rs[1:],
            )
        )
    return out


def in_chapters(item: str, chapters: Sequence[int] | None,
                kinds: Sequence[str] = cfitems.DEFAULT_KINDS) -> bool:
    """Whether a raw item label belongs to one of ``chapters`` (no filter -> True).

    ONE definition of the chapter filter, because it now has to be applied in
    three places at once — the differential entries, an attack run's targets, and
    the expected item universe the task count is reconciled against. Applying it
    to the entries alone is what made ``plan-run --chapters 1`` fail its own
    reconciliation gate ("383 tasks vs 403 items").

    A label with no chapter (``In-text, book p. 18``) is dropped by any filter:
    the flag means "only these chapters' items", and a narrowed run that still
    carried every unanchored In-text target would not be narrowed at all.
    """
    if not chapters:
        return True
    parsed, _ = cfitems.try_parse(item, kinds)
    if parsed is None or parsed.chapter is None:
        return False
    return parsed.chapter in {int(c) for c in chapters}


def _parses(item: str, kinds: Sequence[str]) -> bool:
    parsed, _ = cfitems.try_parse(item, kinds)
    return parsed is not None


def _rows(data) -> list[dict]:
    """Accept a pairs/ artifact either as a bare row list or as its wrapper.

    ``make_queue`` writes ``{created, source_sha, count, ..., items: [...]}``.
    Iterating the wrapper yields its KEYS, which fails far from the mistake
    ("string indices must be integers"), so unwrap it here as well as in the
    command.
    """
    if isinstance(data, Mapping):
        for key in ("items", "targets", "queue", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return list(value)
        return []
    return list(data or [])


def target_entries(targets: Iterable[dict], companion: str, *, source_id: str,
                   offset: int = 0) -> list[Entry]:
    """Normalise ``pairs/<source_id>/targets.<name>.json`` rows into entries."""
    out: list[Entry] = []
    for row in _rows(targets):
        peers = [str(p) for p in (row.get("peers") or [])]
        single = bool(row.get("single_source", not peers))
        out.append(
            Entry(
                item=str(row["item"]),
                repos=[companion],
                decls=_decls_map(row, [companion], companion),
                source_id=str(row.get("source_id") or source_id),
                printed_page=_page_from(row, offset),
                section=row.get("section"),
                single_source=single,
                peers=peers,
                priority=row.get("priority"),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# slugs
# --------------------------------------------------------------------------- #
def assign_slugs(entries: Sequence[Entry], style: str, kinds: Sequence[str]) -> dict[int, str]:
    """Map entry index -> task id, order-independently and without collisions.

    The canonical id comes from the item grammar, PREFIXED BY THE SOURCE
    (``cflib.items.qualified_slug``): a filename is a single string, and
    ``exercise-1-3-19`` names two different items in two sources, so two
    sources' tasks would overwrite each other in one ``tasks/`` directory.

    Two raw labels can still normalise onto one canonical id within a source
    (``In-text, book p. 18`` and ``In-text convention, book p. 18`` are both
    ``In-text p.18``): *every* entry in such a group falls back to the
    raw-derived slug, which is what the original runs used as a filename, so
    the choice does not depend on input order. A collision that survives that is
    an error — merging two items into one task is exactly the silent drop this
    module exists to prevent.
    """
    def _qualify(entry: Entry, tail: str) -> str:
        prefix = cfitems.source_slug(entry.source_id)
        return f"{prefix}--{tail}" if prefix else tail

    canonical: dict[int, str] = {}
    for i, e in enumerate(entries):
        item, _reject = cfitems.try_parse(e.item, kinds)
        tail = cfitems.slug(item, style) if item is not None else cfitems.slug_from_raw(e.item)
        canonical[i] = _qualify(e, tail)
    counts = collections.Counter(canonical.values())
    out: dict[int, str] = {}
    for i, e in enumerate(entries):
        out[i] = (canonical[i] if counts[canonical[i]] == 1
                  else _qualify(e, cfitems.slug_from_raw(e.item)))
    clash: dict[str, list[str]] = collections.defaultdict(list)
    for i, s in out.items():
        clash[s].append(entries[i].qualified if entries[i].source_id else entries[i].item)
    bad = {s: v for s, v in clash.items() if len(v) > 1}
    if bad:
        lines = "; ".join(f"{s} <- {v}" for s, v in sorted(bad.items()))
        raise CfError(
            f"{len(bad)} task id collision(s): {lines}",
            hint="two source items would share one task file; fix the labels in the companion "
                 "or give the queue distinct item ids",
        )
    return out


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _check_yaml_safe(item: str) -> str:
    for ch in _BAD_YAML_CHARS:
        if ch in item:
            raise CfError(
                f"item label {item!r} contains {ch!r}, which cannot go into a quoted YAML title",
                hint="normalise the label in the adapter",
            )
    return item


def _decls_block(decls: dict[str, list[str]], repos: Sequence[str]) -> str:
    lines = []
    for repo in repos:
        names = decls.get(repo) or []
        lines.append(f"    {repo}: " + (", ".join(names) if names else "(none listed)"))
    return "\n".join(lines)


def _peers_line(entry: Entry) -> str:
    if entry.peers:
        return (
            "Peers that also formalise it (reference only, do NOT report defects in them): "
            + ", ".join(entry.peers)
        )
    return (
        "NO other repository formalises this. There is nothing to compare against, so the book\n"
        "is your only check — this is exactly where defects have survived."
    )


def _indent(text: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.rstrip("\n").splitlines())


def render_task(
    entry: Entry,
    task_id: str,
    preset: Preset,
    ctx: dict[str, Any],
    depends_on: list[str],
    tdir: pathlib.Path,
) -> Task:
    """Render one task file's text from ``templates/tasks/*.tmpl``."""
    _check_yaml_safe(entry.item)
    page = entry.printed_page if entry.printed_page is not None else "unknown"
    common = {
        "task_id": task_id,
        "item": entry.item,
        "printed_page": page,
        "priority": tend_priority(entry.priority),
        "depends_on": "[" + ", ".join(depends_on) + "]",
        "repos_inline": ", ".join(entry.repos),
        "repo_count": len(entry.repos),
        "decls_inline": ", ".join(sorted({d for v in entry.decls.values() for d in v})) or "(none listed)",
        "decls_block": _decls_block(entry.decls, entry.repos),
        "run_id": ctx.get("run_id", ""),
        "source_id": entry.source_id or ctx.get("source_id", ""),
        "source_sha": ctx.get("source_shas", {}).get(entry.source_id)
        or ctx.get("source_sha", ""),
        "gate_command": ctx.get("gate_command", ""),
        "kinds_inline": " or ".join(preset.kinds),
        "companion": ctx.get("companion", entry.repos[0] if entry.repos else ""),
        # Asked only when the gate would accept the answer: the single-source task
        # used to invite a page-anchor check on every preset, including the two
        # whose kinds do not list PAGE_ANCHOR.
        "anchor_question": (" Is the page anchor correct?"
                            if preset.wants_page_anchors else ""),
    }
    if preset.task_style == "attack":
        probe = render_file(
            tdir.parent / "prompts" / f"attack-{preset.attack_label}.md.tmpl",
            {
                **common,
                "peers_line": _peers_line(entry),
                "total_functions_sentence": ctx.get("total_functions_sentence", ""),
            },
        )
        text = render_file(
            tdir / "attack.yaml.tmpl",
            {**common, "attack_label": preset.attack_label, "probe_block": _indent(probe, 2)},
        )
        kind = preset.attack_label
    elif entry.single_source or len(entry.repos) < 2:
        text = render_file(tdir / "single-source.yaml.tmpl", common)
        kind = "SINGLE-SOURCE"
    else:
        text = render_file(tdir / "differential.yaml.tmpl", common)
        kind = "DIFFERENTIAL"
    return Task(
        id=task_id,
        item=entry.item,
        kind=kind,
        priority=common["priority"],
        text=text if text.endswith("\n") else text + "\n",
        depends_on=depends_on,
        source_id=entry.source_id,
    )


def make_tasks(
    entries: Sequence[Entry],
    preset: Preset,
    ctx: dict[str, Any] | None = None,
    *,
    kinds: Sequence[str] = cfitems.DEFAULT_KINDS,
    max_open_tasks: int | None = None,
    expected_items: Iterable[str] | None = None,
    allow_missing_decls: bool = False,
) -> list[Task]:
    """Render every entry into a task, then reconcile.

    ``max_open_tasks`` chains ``depends_on`` so tend never holds more than N
    worktrees at once (the differential run created 382 worktrees and 1.1 GB in
    eleven minutes with a single worker allowed).

    ``expected_items`` — usually ``items.json ∩ (any repo)`` — turns the count
    reconciliation into a set reconciliation and names what is missing.  Its
    entries may be bare labels (one source) or ``<source_id>::<item>``.
    """
    ctx = dict(ctx or {})
    tdir = pathlib.Path(ctx.get("templates_dir") or (templates_dir() / "tasks"))
    slugs = assign_slugs(entries, preset.slug_style, kinds)

    if not allow_missing_decls:
        bare = [e.item for e in entries if not any(e.decls.get(r) for r in e.repos)]
        if bare:
            raise CfError(
                f"{len(bare)} entr(ies) name no declaration: {bare[:8]}",
                hint="a task that cannot tell the worker which Lean names to read wastes a "
                     "worker; run `cf pair` so queue.json/targets.*.json carry decls, or pass "
                     "allow_missing_decls=True deliberately",
            )

    tasks: list[Task] = []
    for i, entry in enumerate(entries):
        if preset.prioritise and not entry.priority:
            item, _ = cfitems.try_parse(entry.item, kinds)
            kind = item.kind if item is not None else entry.item.split()[0]
            entry.priority = priority_for(kind, entry.single_source, entry.section)
        depends = []
        if max_open_tasks and i >= max_open_tasks:
            depends = [slugs[i - max_open_tasks]]
        tasks.append(render_task(entry, slugs[i], preset, ctx, depends, tdir))

    reconcile(entries, tasks, expected_items=expected_items)
    return tasks


def _key(source_id: str, item: str) -> str:
    """The reconciliation key: qualified when a source is known, else the label."""
    return cfitems.qualified_item(source_id, item) if source_id else cfitems.clean(item)


def reconcile(
    entries: Sequence[Entry],
    tasks: Sequence[Task],
    *,
    expected_items: Iterable[str] | None = None,
) -> None:
    """Refuse a task set that lost an item. Raises ``CfError`` with the names.

    Everything is compared on ``(source_id, item)``.  On the bare label, two
    sources' ``Exercise 1.3.19`` would cancel out — one missing and one
    unexpected would net to zero and the reconciliation would report success
    over a run that audited the wrong edition of one of them.
    """
    if len(tasks) != len(entries):
        planned = {e.qualified if e.source_id else e.item for e in entries}
        written = {t.qualified if t.source_id else t.item for t in tasks}
        raise CfError(
            f"task count {len(tasks)} != planned {len(entries)}",
            hint="dropped: " + ", ".join(sorted(planned - written)),
        )
    ids = collections.Counter(t.id for t in tasks)
    dupes = [i for i, n in ids.items() if n > 1]
    if dupes:
        raise CfError(f"duplicate task ids: {sorted(dupes)}")
    if expected_items is not None:
        sources = {t.source_id for t in tasks}
        only = next(iter(sources)) if len(sources) == 1 else ""
        expected = {
            str(i) if cfitems.QUALIFIED_SEP in str(i) else _key(only, str(i))
            for i in expected_items
        }
        got = {_key(t.source_id, t.item) for t in tasks}
        missing, extra = sorted(expected - got), sorted(got - expected)
        if missing or extra:
            raise CfError(
                f"task set does not match the expected item universe "
                f"({len(got)} tasks vs {len(expected)} items); "
                f"missing {len(missing)}: {missing[:10]}; "
                f"unexpected {len(extra)}: {extra[:10]}",
                hint="an item that reaches no task is the 382-vs-383 defect this gate exists "
                     "to catch; re-run `cf pair` and compare pairs/reconciliation.json",
            )


def write_tasks(tasks: Sequence[Task], out_dir: pathlib.Path) -> list[pathlib.Path]:
    """Write ``<out_dir>/<id>.yaml`` for each task; idempotent."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t in tasks:
        p = out_dir / f"{t.id}.yaml"
        if not p.exists() or p.read_text(encoding="utf-8") != t.text:
            p.write_text(t.text, encoding="utf-8")
        written.append(p)
    stale = {p.name for p in out_dir.glob("*.yaml")} - {p.name for p in written}
    if stale:
        raise CfError(
            f"{len(stale)} stale task file(s) in {out_dir}: {sorted(stale)[:5]}",
            hint="this run directory was planned from a different queue; use a new run id",
        )
    return written
