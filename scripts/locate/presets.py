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


#: the account's concurrent-session cap when the workspace records none
#: (references/traps.md C3; ``cf run`` refuses a plan over it)
DEFAULT_SESSION_CAP = 16


def concurrency(preset: Preset, workspace, *, workers: int | None = None,
                reviewers: int | None = None) -> dict:
    """The run's worker and reviewer limits, each with where it came from.

    ``--workers``/``--reviewers`` first, then the workspace's
    ``defaults.max_concurrent_workers``/``max_concurrent_reviewers`` -- the
    operator's setting for this account and host -- and the preset's own numbers
    only when the workspace says nothing.  The planner used to write the preset's
    8/4 into ``run.yaml`` and ``config.yaml`` whatever the workspace said, and
    ``cf run`` prefers ``run.yaml``, so a workspace that asked for 6 workers got
    8 at the raise stage (field defect P3).  Returns ``{"workers", "reviewers",
    "source": {"workers", "reviewers"}, "warnings"}``.
    """
    out: dict = {"source": {}, "warnings": []}
    for role, flag, key, fallback in (
            ("workers", workers, "max_concurrent_workers", preset.max_workers),
            ("reviewers", reviewers, "max_concurrent_reviewers", preset.max_reviewers)):
        if flag is not None:
            value, source = flag, f"--{role}"
        elif workspace.default(key) is not None:
            value, source = workspace.default(key), f"workspace.defaults.{key}"
        else:
            value, source = fallback, f"preset.{preset.name}"
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = 0
        if number < 1 or isinstance(value, bool) or str(number) != str(value).strip():
            raise CfError(f"{source} is {value!r}; a concurrency limit is a whole number >= 1",
                          hint=f"fix {source}, or pass --{role} N")
        out[role] = number
        out["source"][role] = source
    cap = int(workspace.default("max_concurrent_sessions", DEFAULT_SESSION_CAP) or 0)
    if cap and out["workers"] + out["reviewers"] > cap:
        out["warnings"].append(
            f"{out['workers']} workers + {out['reviewers']} reviewers = "
            f"{out['workers'] + out['reviewers']} concurrent sessions, over the account cap of "
            f"{cap}: `cf run` will refuse to raise to it (references/traps.md C3)")
    return out
