"""Render the five run prompts from one template set plus the companions' conventions.

Ported from the prompt files of the five orchestration roots retrieved from
ffmain (``tao-diff-tend/prompts/*.md`` and the four siblings). Those were five
hand-maintained copies of one text: ``worker.md`` and ``worker-revision.md``
were byte-identical everywhere, and ``worker-system.md`` / ``reviewer-system.md``
/ ``reviewer.md`` differed only by appended blocks (see templates/PRESETS.md).
Here there is one template per prompt and the blocks are preset flags.

The house-conventions list — the thing the CONVENTION-vs-CONTENT judgement
turns on, and the reason ~40% of first-pass findings were wrong on the one
corpus that has been measured — is **not** in the templates. It is read from
each companion's ``conventions.md`` (``templates/conventions.md.example`` is the
contract: numbered ``## N. Title`` sections), so the skill repo ships no
repo-specific or private convention text.

Sections consumed here:

* ``1. Total functions and junk values``  -> the junk-value question in the vac probe
* ``2. Standing type and structure conventions`` -> the CONVENTION_ONLY bullets
* ``3. Scope`` -> "stops at §1.4" style notes
* ``4. Sorry policy`` -> permanent exercise sorries
* ``7. Known-and-accepted deltas`` -> the refuted-on-sight block in worker-system

``5. Naming and attribution`` and ``6. Errata disposition`` are deliberately NOT
parsed into slots: the whole file is inlined into the adjudication COMMON block
(``scripts/ledger/workflow_args.py``), so a second slot for them would be a
second copy that could disagree with the first.

Two further rules this module enforces, both of them about not lying to a model:

* no calibration figure is asserted as local unless it was measured locally.
  ``calibration_note`` and ``prior_finding_mix`` are rendered from the
  workspace's own ledger when it holds adjudicated rows, and otherwise carry the
  imported figure WITH its sample size and the campaign it came from;
* what the prompt invites is a function of what the gate accepts. The
  CONTENT_DELTA list, the citation ban and the "did the worker miss a page
  anchor" question all come from ``preset.kinds``, so a run whose gate rejects
  PAGE_ANCHOR cannot ask a worker for one (22 of 40 batch-1 differential
  findings were pagination noise).
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Iterable, Mapping, Sequence

from cflib import vocab
from cflib.errors import CfError
from cflib.paths import templates_dir

from .presets import Preset
from .render import render_file

PROMPT_FILES = (
    "worker-system.md",
    "worker.md",
    "worker-revision.md",
    "reviewer-system.md",
    "reviewer.md",
)

#: conventions.md section -> the slot it feeds.  Nothing is parsed that no
#: template reads: `5. Naming` and `6. Errata disposition` reach the models
#: through the whole-file inline in the adjudication COMMON block instead.
SECTION_KEYS = {
    "total_functions": ("total function", "junk value"),
    "standing": ("standing type", "standing convention", "structure convention"),
    "scope": ("scope",),
    "sorry": ("sorry",),
    "known_deltas": ("known-and-accepted", "known and accepted"),
}

#: the sections companion_bullet() turns into the CONVENTION_ONLY line; a file
#: whose every one of these is empty is not a confirmed conventions list.
CONSUMED_SECTIONS = ("total_functions", "standing", "sorry", "scope")

_EXAMPLE = re.compile(r"^\(example\)\s*", re.I)
_CONFIRMED_BY = re.compile(r"(?im)^\s*confirmed_by\s*:\s*(.+?)\s*$")
_CONFIRMED_AT = re.compile(r"(?im)^\s*confirmed_at\s*:\s*(.+?)\s*$")
#: the value the onboarding contract reserves for "nobody confirmed this"
#: (references/onboarding-contract.md S5).
UNCONFIRMED = "unconfirmed"

#: The imported calibration figure and, inseparably, what it was measured on:
#: references/method.md S4 -- 4 of 10 adjudicated rows were house convention, on
#: ONE chapter of ONE book.  Nothing may render this as a property of the corpus
#: being audited.
IMPORTED_CALIBRATION = {
    "wrong": 4,
    "n": 10,
    "campaign": "one chapter of one book, Tao's measure theory",
}
#: Same, for the page-anchor mix that justifies the citation ban
#: (references/method.md S1: 22 of 40 differential-flagged items were pagination).
IMPORTED_ANCHOR_MIX = {"anchors": 37, "statements": 4}

#: CONTENT_DELTA illustrations that belong to no particular book.  A per-source
#: list replaces them (build_context(examples=...)), so a group-theory companion
#: is not taught to look for dimension hypotheses.
NEUTRAL_EXAMPLES = (
    "a statement proved in less generality than the book's, or in more without "
    "specialising back to it",
    "a hypothesis the book does not impose, or one it does",
    "a conclusion at a different strength from the book's",
)


# --------------------------------------------------------------------------- #
# conventions.md
# --------------------------------------------------------------------------- #
def parse_conventions(text: str) -> dict[str, list[str]]:
    """Split a ``conventions.md`` into {slot: [bullet, ...]}.

    Headings are ``## 1. Total functions and junk values``; the leading number
    is ignored so a companion may reorder or drop sections. Only bullet lines
    are collected — prose under a heading is guidance for the human writing the
    file, not text to paste into a worker's system prompt. Placeholder bullets
    written as ``(example) …`` in the shipped template are dropped, so an
    unfilled conventions.md produces an empty list rather than fake conventions.
    """
    out: dict[str, list[str]] = {k: [] for k in SECTION_KEYS}
    current: str | None = None
    for line in (text or "").splitlines():
        m = re.match(r"^##\s+(?:\d+\.\s*)?(.+?)\s*$", line)
        if m:
            title = m.group(1).lower()
            current = None
            for slot, needles in SECTION_KEYS.items():
                if any(n in title for n in needles):
                    current = slot
                    break
            continue
        if current and re.match(r"^\s*[-*]\s+", line):
            bullet = re.sub(r"^\s*[-*]\s+", "", line).strip()
            if _EXAMPLE.match(bullet):
                continue
            if bullet:
                out[current].append(bullet)
    return out


def companion_bullet(name: str, conventions: Mapping[str, list[str]]) -> str:
    """One ``- `repo`: a; b; c`` line, as the original prompts carried."""
    parts: list[str] = []
    for slot in ("total_functions", "standing", "sorry", "scope"):
        parts.extend(conventions.get(slot) or [])
    body = "; ".join(p.rstrip(".") for p in parts) if parts else (
        "no conventions recorded yet — treat every delta as content until the owner confirms one"
    )
    return f"- `{name}`: {body}."


def shared_bullet(per_repo: Mapping[str, Mapping[str, list[str]]], override: str | None = None) -> str:
    """The "Any repo:" line.

    An explicit workspace-level text wins. Otherwise it is the de-duplicated
    union of every companion's standing conventions: derived from the confirmed
    per-repo lists rather than invented here, because a shared list invented in
    the skill would be both Tao-specific and unconfirmed.
    """
    if override:
        return override.strip()
    seen: list[str] = []
    for conv in per_repo.values():
        for bullet in conv.get("standing") or []:
            key = bullet.rstrip(".").lower()
            if key not in [s.rstrip(".").lower() for s in seen]:
                seen.append(bullet)
    if not seen:
        return (
            "bundled vs unbundled hypotheses; naming; splitting one book item across several "
            "declarations; a strictly MORE general statement that specialises to the book's "
            "(record it, mark CONVENTION_ONLY, note \"stronger\")"
        )
    return "; ".join(s.rstrip(".") for s in seen)


def load_conventions(path: pathlib.Path | None) -> dict[str, list[str]]:
    """Read one companion's conventions.md (missing file -> empty sections)."""
    if path is None or not pathlib.Path(path).exists():
        return {k: [] for k in SECTION_KEYS}
    return parse_conventions(pathlib.Path(path).read_text(encoding="utf-8"))


def confirmed_by(text: str) -> str:
    """The ``confirmed_by:`` front-matter value, or "" when there is none.

    ``unconfirmed`` is the value the onboarding contract reserves for a repo with
    no reachable owner, and it counts as absent here: the point of the field is
    that a human said the list is right.
    """
    found = _CONFIRMED_BY.search(text or "")
    value = (found.group(1).strip() if found else "").strip("'\" ")
    return "" if value.lower() in ("", UNCONFIRMED, "none", "null", "todo") else value


def conventions_status(path: pathlib.Path | None) -> dict[str, Any]:
    """Is this companion's conventions.md confirmed, and if not, why not.

    Returns ``{exists, confirmed_by, confirmed_at, empty, ok, reason}``.  The
    default state of a new companion is NOT a missing file -- ``cf onboard``
    copies the template in -- so "missing" is the rare case and an existing file
    whose every consumed section is still ``(example)`` placeholders is the
    common one.  Both render the same self-contradicting prompt ("a first pass
    was wrong 40% of the time by over-calling content" next to "treat every
    delta as content"), so both are reported here as unconfirmed.
    """
    p = pathlib.Path(path) if path else None
    if p is None or not p.exists():
        return {"exists": False, "confirmed_by": "", "confirmed_at": "", "empty": True,
                "ok": False, "reason": "no conventions.md"}
    text = p.read_text(encoding="utf-8")
    who = confirmed_by(text)
    at = _CONFIRMED_AT.search(text)
    sections = parse_conventions(text)
    empty = not any(sections.get(s) for s in CONSUMED_SECTIONS)
    if not who:
        reason = "no confirmed_by: front-matter"
    elif empty:
        reason = "every section the prompts read is empty once `(example)` bullets are dropped"
    else:
        reason = ""
    return {
        "exists": True,
        "confirmed_by": who,
        "confirmed_at": (at.group(1).strip() if at else ""),
        "empty": empty,
        "ok": not reason,
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# calibration: measured here, or imported and labelled as such
# --------------------------------------------------------------------------- #
def _ledger_rows(ledger_dir: pathlib.Path | str | None) -> list[dict]:
    """Every adjudicated row the workspace's ledger holds (``_meta`` lines skipped)."""
    if not ledger_dir:
        return []
    root = pathlib.Path(ledger_dir)
    if not root.is_dir():
        return []
    rows: list[dict] = []
    for name in sorted(root.glob("accepted.*.jsonl")) + sorted(root.glob("refuted.*.jsonl")):
        for line in name.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "_meta" not in row and row.get("verdict"):
                rows.append(row)
    return rows


def ledger_calibration(ledger_dir: pathlib.Path | str | None) -> dict | None:
    """This workspace's own first-pass error rate, or None when nothing is adjudicated.

    Counted over every row in ``ledger/{accepted,refuted}.*.jsonl``: ``wrong`` is
    a verdict that did not survive as a defect against the companion (CONVENTION
    or REFUTED), which is the same thing references/method.md S4 counts for the
    imported figure.  ``anchors``/``statements`` is the page-anchor mix.

    Returning None is the point of the function: a prompt must not assert a
    measurement about a corpus nobody has measured.
    """
    rows = _ledger_rows(ledger_dir)
    if not rows:
        return None
    wrong = sum(1 for r in rows if str(r.get("verdict")) in (vocab.CONVENTION, vocab.REFUTED))
    anchors = sum(1 for r in rows if str(r.get("kind")) == vocab.PAGE_ANCHOR)
    return {
        "n": len(rows),
        "wrong": wrong,
        "anchors": anchors,
        "statements": len(rows) - anchors,
    }


def calibration_note(local: Mapping[str, Any] | None) -> str:
    """The CONVENTION-vs-CONTENT calibration sentence.

    Local when the workspace has adjudicated rows of its own; otherwise the
    imported figure, named as imported and carrying its sample size.  The old
    text said "on this same corpus" on every book, which is false everywhere but
    the one campaign it was measured on -- and it is the sentence that sets how
    sceptical the model is.
    """
    if local and local.get("n"):
        n, wrong = int(local["n"]), int(local["wrong"])
        pct = round(100.0 * wrong / n)
        return (f"Measured in this workspace's own ledger: {wrong} of {n} adjudicated "
                f"findings so far (about {pct}%) turned out to be house convention or were "
                "refuted outright, rather than content bugs — nearly always in that "
                "direction, calling a convention a defect.")
    imported = IMPORTED_CALIBRATION
    return (f"No calibration has been measured on this corpus yet. On a different corpus "
            f"— {imported['campaign']} — {imported['wrong']} of {imported['n']} adjudicated "
            "first-pass findings were house convention rather than content bugs. Treat that "
            "as an order of magnitude imported from elsewhere, not as a fact about this "
            "material, and read it as a reason to check a convention list before reporting.")


def prior_finding_mix(local: Mapping[str, Any] | None) -> str:
    """The sentence that justifies refusing citation findings.

    Same rule as :func:`calibration_note`: the workspace's own mix when it has
    one, else the imported 37-vs-4 with its provenance. On a book whose real
    defect population IS pagination, an imported ratio must not be the argument.
    """
    if local and local.get("n"):
        return (f"In this workspace's own ledger, {local['anchors']} adjudicated finding(s) "
                f"were page anchors and {local['statements']} were statement-level; the cheap "
                "finding crowds out the real one, and anchors are checked mechanically here.")
    mix = IMPORTED_ANCHOR_MIX
    return (f"In the one campaign measured so far — {IMPORTED_CALIBRATION['campaign']} — a "
            f"differential run returned {mix['anchors']} page-anchor findings against the "
            f"audited repository and {mix['statements']} statement-level ones — the cheap "
            "finding crowded out the real one. That figure is imported, not a measurement of "
            "this corpus; page anchors are checked mechanically here either way.")


# --------------------------------------------------------------------------- #
# slots that must agree with the gate
# --------------------------------------------------------------------------- #
def content_delta_list(preset: Preset, examples: Sequence[str] | None = None) -> str:
    """The CONTENT_DELTA sentence, derived from what the gate accepts.

    The page-anchor clause appears only when PAGE_ANCHOR is one of this run's
    kinds. It used to be hard-coded, so both differential presets asked for the
    cheapest finding their own gate does not list.
    """
    parts = [
        "different hypotheses (missing, extra, weaker, stronger)",
        "generality that does not specialise correctly",
        "a weaker or different conclusion",
        "different quantifier scope",
        "a wrong endpoint or normalisation convention",
        "a vacuous or trivially-true statement",
        'a "definition" that is not the book\'s definition',
    ]
    if preset.wants_page_anchors:
        parts.append("a wrong page anchor")
    body = "; ".join(parts) + "."
    shapes = [e.rstrip(".") for e in (examples or NEUTRAL_EXAMPLES) if str(e).strip()]
    if shapes:
        body += "\n\nWhat that looks like in this material: " + "; ".join(shapes) + "."
    return body


def missed_list(preset: Preset) -> str:
    """What the reviewer is asked to look for in the worker's omissions."""
    parts = ["an unmatched declaration", "a sub-part `(i)`/`(ii)` present in one repo only"]
    if preset.wants_page_anchors:
        parts.append("a wrong page anchor")
    return ", ".join(parts) + "."


def citation_ban_kinds(preset: Preset) -> str:
    """"`PAGE_ANCHOR`" / "`PAGE_ANCHOR` or `COVERAGE`" — whichever the gate rejects."""
    names = [f"`{k}`" for k in preset.banned_citation_kinds]
    return " or ".join(names)


def finish_sentence(preset: Preset) -> str:
    """The ONE way this bridge reads a result, as a sentence.

    One rendered prompt set used to name three: a `final_result` tool the
    agentbox bridges do not expose, `.tend/agent_result.json`, and stdout.
    """
    channel = preset.finish_channel
    if channel == "final_result":
        return "Call `final_result` exactly once with your structured result."
    if channel == "stdout":
        return ("There is no `final_result` tool on this bridge: finish by printing one JSON "
                "result object as the last thing in your output, in the shape the \"How to "
                "finish\" section of your task prompt gives.")
    return ("There is no `final_result` tool on this bridge: finish by writing "
            "`.tend/agent_result.json` in the worktree, in the shape the \"How to finish\" "
            "section of your task prompt gives.")


def example_deltas(examples: Sequence[str] | None = None) -> str:
    """The "a compiler cannot see this" illustration, per source.

    The shipped text named one book's objects (`ℝ` vs `ℝᵈ`, a dropped `d ≥ 1`);
    a companion of a different book needs its own, and the neutral default is
    what it gets until somebody records them.
    """
    shapes = [e.rstrip(".") for e in (examples or NEUTRAL_EXAMPLES) if str(e).strip()]
    return "; ".join(shapes)


def known_deltas_block(per_repo: Mapping[str, Mapping[str, list[str]]]) -> str:
    """The refuted-on-sight list: section 7 of every companion's conventions.md.

    render_prompts' docstring promised this block and never rendered it, so the
    one section that most cheaply prevents a repeat false positive reached the
    worker nowhere.
    """
    lines: list[str] = []
    for name, conv in per_repo.items():
        for bullet in conv.get("known_deltas") or []:
            lines.append(f"- `{name}`: {bullet.rstrip('.')}.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# context + rendering
# --------------------------------------------------------------------------- #
def build_context(
    preset: Preset,
    *,
    run_id: str,
    source_id: str,
    book_citation: str,
    book_text: str,
    source_sha: str,
    page_offset: int,
    companions: Iterable[Mapping[str, Any]],
    source_edition: str = "",
    gate_command: str,
    data_dir: str = "data",
    errata_path: str = "",
    images_dir: str = "",
    primary: str = "",
    conventions_shared_override: str | None = None,
    ledger_dir: pathlib.Path | str | None = None,
    examples: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build the slot dictionary the prompt templates need.

    ``companions`` rows: ``{name, path, files?, conventions_md?}``.

    ``ledger_dir`` is the workspace's ``ledger/``: when it holds adjudicated rows
    the calibration sentences are rendered from THEM, and when it does not they
    say so and name the campaign the imported figure came from.  ``examples`` are
    this source's own illustrations of a content delta; without them the prompt
    uses book-neutral shapes rather than another book's objects.

    The prompt names the SOURCE, not "the book": a worker told to check a quote
    against "the book" when two editions of one work are on the host has no way
    to know which, and the anchor edition is not always the one the companion
    ships (docs/design/multi-source-model.md S4.6).  ``source_id``, the edition
    label and the first eight characters of ``source_sha`` go in the header.
    """
    rows = list(companions)
    if not rows:
        raise CfError("no companions given; a prompt cannot name the repositories under audit")
    per_repo: dict[str, dict[str, list[str]]] = {}
    lines: list[str] = []
    listing: list[str] = []
    for row in rows:
        name = str(row["name"])
        conv = load_conventions(row.get("conventions_md"))
        per_repo[name] = conv
        lines.append(companion_bullet(name, conv))
        where = str(row.get("path") or "")
        files = str(row.get("files") or "")
        listing.append(f"- `{name}` — `{where}`" + (f", files `{files}`" if files else ""))
    primary = primary or str(rows[0]["name"])
    total = per_repo.get(primary, {}).get("total_functions") or []
    local = ledger_calibration(ledger_dir)
    shapes = list(examples or ())
    return {
        "run_id": run_id,
        "preset": preset.name,
        "source_id": source_id,
        "source_edition": source_edition or source_id,
        "source_sha": source_sha,
        "source_sha8": str(source_sha or "")[:8],
        "book_citation": book_citation,
        "book_text": book_text,
        "page_offset": page_offset,
        "images_dir": images_dir or "(no page images in this run)",
        "errata_path": errata_path or "(none fetched)",
        "data_dir": data_dir,
        "companion_count": len(rows),
        "companion_phrase": f"{len(rows)} INDEPENDENT Lean 4 formalization"
                            + ("s" if len(rows) != 1 else ""),
        "companion_list": "\n".join(listing),
        "companion_inline": ", ".join(f"`{r['name']}` = `{r.get('path','')}`" for r in rows),
        "conventions_per_repo": "\n".join(lines),
        "conventions_shared": shared_bullet(per_repo, conventions_shared_override),
        "total_functions_sentence": (
            "This repo: " + "; ".join(t.rstrip(".") for t in total) + "."
            if total
            else "Lean functions are total, so a value may exist where the book says nothing."
        ),
        "gate_command": gate_command,
        "primary_repo": f"`{primary}`",
        "companion": primary,
        "example_item": "<the book item named in your task file>",
        "example_repo": primary,
        "verdicts_inline": " | ".join(preset.verdicts),
        "kinds_inline": " | ".join(preset.kinds),
        # calibration: measured here, or imported and labelled as such
        "calibration_note": calibration_note(local),
        "prior_finding_mix": prior_finding_mix(local),
        "calibration_local": bool(local),
        # what the prompt may ask for, derived from the gate's kinds
        "content_delta_list": content_delta_list(preset, shapes),
        "missed_list": missed_list(preset),
        "citation_ban_kinds": citation_ban_kinds(preset),
        "example_deltas": example_deltas(shapes),
        "known_deltas": known_deltas_block(per_repo),
        # the one finish channel this bridge reads
        "finish_sentence": finish_sentence(preset),
        "finish_via_tool": preset.finish_channel == "final_result",
        "finish_via_file": preset.finish_channel == "agent_result",
        "finish_via_stdout": preset.finish_channel == "stdout",
        # preset blocks
        "witness_required": preset.witness_required,
        "page_images": preset.page_images,
        "citation_ban": preset.citation_ban,
        "no_defect_verdict": preset.no_defect_verdict,
        "approve_guidance": preset.approve_guidance,
        "final_line": preset.final_line,
    }


def render_prompts(
    preset: Preset,
    ctx: Mapping[str, Any],
    out_dir: pathlib.Path | None = None,
    *,
    tdir: pathlib.Path | None = None,
) -> dict[str, str]:
    """Render every prompt; write them when ``out_dir`` is given."""
    tdir = pathlib.Path(tdir or (templates_dir() / "prompts"))
    out: dict[str, str] = {}
    for name in PROMPT_FILES:
        out[name] = render_file(tdir / f"{name}.tmpl", ctx)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, text in out.items():
            (out_dir / name).write_text(text, encoding="utf-8")
    return out
