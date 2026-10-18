"""The three locate presets: L3a inventory, L3b locate, L5 challenge.

New code, for docs/proposals/wikipedia-locate.md section 3 ("three presets on a
new locate planner").  Each is a ``plan.presets.Preset`` -- the same dataclass,
so ``plan.make_tend_root`` renders their tend roots on the existing
gate/bridge/harvest machinery -- but they live here, not in
``plan.presets.PRESETS``: a locate run is planned from a sourceset and a
universe by ``cf plan-locate``, never from a companion by ``cf plan-run``,
which refuses their names and says which command plans them.

What differs between the three is data: the run mode (``cflib.vocab``'s locate
modes, whose kinds are the gate's), the task bound, the concurrency (worker
plus reviewer sessions stay under the bridge's 15 slots, proposal section 6),
and the prompt the worker reads.  The root bound every preset shares is
``cflib.vocab.MAX_TASKS_PER_ROOT`` (proposal section 6, Roots).
"""
from __future__ import annotations

from dataclasses import dataclass

from cflib import vocab
from cflib.errors import CfError

from plan.presets import Preset

@dataclass(frozen=True)
class LocateShape:
    """What a locate preset adds to a ``Preset``: its task prompt and its bound."""

    worker_template: str
    max_per_task: int
    title: str


def _preset(mode: str, *, workers: int, reviewers: int, note: str) -> Preset:
    return Preset(
        name=mode,
        mode=mode,
        kinds=tuple(sorted(vocab.kinds_for_mode(mode))),
        verdicts=(),
        witness_required=False,
        book_access="text",
        bridge="agentbox-claude",
        model=None,
        task_style="locate",
        slug_style="dash",
        max_workers=workers,
        max_reviewers=reviewers,
        note=note,
    )


LOCATE_PRESETS: dict[str, Preset] = {
    vocab.LOCATE_INVENTORY: _preset(
        vocab.LOCATE_INVENTORY, workers=8, reviewers=4,
        note="L3a: one task per source; the deterministic finders' passages pre-filled, "
             "the enumerator adds the rest; the gate freezes the passage table"),
    vocab.LOCATE: _preset(
        vocab.LOCATE, workers=8, reviewers=4,
        note="L3b: tasks of at most 8 formal_math passages of a frozen inventory; each "
             "passage ends located, absent or unresolved"),
    vocab.LOCATE_CHALLENGE: _preset(
        vocab.LOCATE_CHALLENGE, workers=6, reviewers=3,
        note="L5: batches of about 20 records; per-record verdicts"),
}

SHAPES: dict[str, LocateShape] = {
    vocab.LOCATE_INVENTORY: LocateShape("locate-inventory.md.tmpl", 1, "inventory"),
    vocab.LOCATE: LocateShape("locate.md.tmpl", vocab.MAX_PASSAGES_PER_LOCATE_TASK, "locate"),
    vocab.LOCATE_CHALLENGE: LocateShape("locate-challenge.md.tmpl",
                                        vocab.CHALLENGE_BATCH_RECORDS, "challenge"),
}


def get(name: str) -> Preset:
    try:
        return LOCATE_PRESETS[name]
    except KeyError:
        raise CfError(f"unknown locate preset {name!r}",
                      hint="one of: " + ", ".join(sorted(LOCATE_PRESETS))) from None


def shape(name: str) -> LocateShape:
    get(name)
    return SHAPES[name]
