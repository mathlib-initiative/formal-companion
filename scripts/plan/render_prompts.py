"""Render the five run prompts from one template set plus the companions' conventions.

Ported from the prompt files of the five orchestration roots retrieved from
ffmain (``tao-diff-tend/prompts/*.md`` and the four siblings). Those were five
hand-maintained copies of one text: ``worker.md`` and ``worker-revision.md``
were byte-identical everywhere, and ``worker-system.md`` / ``reviewer-system.md``
/ ``reviewer.md`` differed only by appended blocks (see templates/PRESETS.md).
Here there is one template per prompt and the blocks are preset flags.

The house-conventions list — the thing the CONVENTION-vs-CONTENT judgement
turns on, and the reason ~40% of first-pass findings were wrong — is **not** in
the templates. It is read from each companion's ``conventions.md``
(``templates/conventions.md.example`` is the contract: numbered ``## N. Title``
sections), so the skill repo ships no repo-specific or private convention text.

Sections consumed here:

* ``1. Total functions and junk values``  -> the junk-value question in the vac probe
* ``2. Standing type and structure conventions`` -> the CONVENTION_ONLY bullets
* ``3. Scope`` -> "stops at §1.4" style notes
* ``4. Sorry policy`` -> permanent exercise sorries
* ``7. Known-and-accepted deltas`` -> refuted-on-sight list
"""

from __future__ import annotations

import pathlib
import re
from typing import Any, Iterable, Mapping

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

#: conventions.md section -> the slot it feeds.
SECTION_KEYS = {
    "total_functions": ("total function", "junk value"),
    "standing": ("standing type", "standing convention", "structure convention"),
    "scope": ("scope",),
    "sorry": ("sorry",),
    "naming": ("naming", "attribution"),
    "errata": ("errata",),
    "known_deltas": ("known-and-accepted", "known and accepted"),
}

_EXAMPLE = re.compile(r"^\(example\)\s*", re.I)


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

    An explicit campaign-level text wins. Otherwise it is the de-duplicated
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


# --------------------------------------------------------------------------- #
# context + rendering
# --------------------------------------------------------------------------- #
def build_context(
    preset: Preset,
    *,
    run_id: str,
    book_citation: str,
    book_text: str,
    book_sha: str,
    page_offset: int,
    companions: Iterable[Mapping[str, Any]],
    gate_command: str,
    data_dir: str = "data",
    errata_path: str = "",
    images_dir: str = "",
    primary: str = "",
    conventions_shared_override: str | None = None,
) -> dict[str, Any]:
    """Build the slot dictionary the prompt templates need.

    ``companions`` rows: ``{name, path, files?, conventions_md?}``.
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
    return {
        "run_id": run_id,
        "preset": preset.name,
        "book_citation": book_citation,
        "book_text": book_text,
        "book_sha": book_sha,
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
        # preset blocks
        "witness_required": preset.witness_required,
        "page_images": preset.page_images,
        "citation_ban": preset.citation_ban,
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
