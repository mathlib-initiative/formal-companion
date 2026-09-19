"""Every model id the planner can hand to a bridge, in one table.

New code (design critique: "the only substrate that has ever produced findings
depends on two tools the repo never names a source or version for"). The ids
were spread over ``plan/presets.py`` (the codex presets' pinned id) and
``plan/make_tend_root.py`` (the tend-agent backend's defaults), so a renamed
tier meant hunting literals through two modules, and the miss was silent: a run
planned against a model that no longer exists fails on the host, per agent.

**When a tier is renamed or retired**, change it in ONE of two places:

* ``DEFAULTS`` below, for the shipped default — and nowhere else in ``scripts/``;
* the workspace's own ``models:`` block, which wins over ``DEFAULTS`` for the
  same key. ``cf plan-run`` resolves the run's model through :func:`resolve`,
  records the id **and where it came from** in ``run.yaml``, and warns when the
  id is not one the workspace names (hard rule 8 is a budget convention, so it
  warns rather than refusing).

The Workflow scripts under ``workflows/`` pin ``model: 'opus'`` per phase and
are not planner inputs; they are named here only so a reader knows that is the
other place an id lives.

Spec: docs/design/multi-source-model.md S7.2 (run.yaml records what produced a
finding).
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

#: The shipped default per key. A workspace ``models:`` entry of the same name
#: overrides it.
DEFAULTS: dict[str, str] = {
    # The tend-agent backend's own model block (templates/tend/*-agent.yaml.tmpl).
    # None of the five recorded runs used that backend, but `--bridge tend-agent`
    # is supported, so the generated yaml has to name something real.
    "tend_worker": "claude-sonnet-4-5",
    "tend_reviewer": "claude-sonnet-4-5",
    # The calibration pair's second model: the two codex-bridge presets were
    # measured with it, which is why they pin an id instead of taking the
    # bridge's default.
    "worker_codex": "gpt-5.6-sol",
}

#: ``models:`` keys a workspace sets for its own roles.
WORKSPACE_ROLES = ("worker", "reviewer", "skeptic", "adjudicator", "calibration")

#: bridge -> the key that names the worker model for it. A bridge absent here
#: takes the workspace's ``models.worker`` (or the bridge's own default).
BRIDGE_MODEL_KEY: dict[str, str] = {"agentbox-codex": "worker_codex"}

_MODEL_LINE = re.compile(r"^\s*model_name:\s*(\S+)\s*$", re.M)


def default(key: str) -> str | None:
    """The shipped id for ``key`` (None when this table does not name one)."""
    return DEFAULTS.get(key)


def configured(workspace: Any, key: str) -> str | None:
    """The workspace's own id for ``key``, if its ``models:`` block sets one."""
    if workspace is None:
        return None
    try:
        value = workspace.models.get(key)
    except AttributeError:  # pragma: no cover - a mapping-shaped stand-in
        value = (workspace or {}).get(key)
    return str(value) if value else None


def for_key(workspace: Any, key: str) -> str | None:
    """``workspace.models[key]`` if set, else the shipped default."""
    return configured(workspace, key) or default(key)


def resolve(*, requested: str | None, preset: Any, workspace: Any) -> tuple[str | None, str]:
    """The run's worker model, and the path it was resolved by.

    Precedence, most explicit first: ``--model``; the workspace's override for
    this bridge's key (so rotating the codex tier is config, not a code edit);
    the preset's pinned id (a named calibration point); the workspace's
    ``models.worker``.  ``(None, "bridge default")`` when nothing names one —
    which is honest, and is what ``run.yaml`` then records.
    """
    if requested:
        return str(requested), "--model"
    key = BRIDGE_MODEL_KEY.get(getattr(preset, "bridge", "") or "")
    if key:
        override = configured(workspace, key)
        if override:
            return override, f"workspace.models.{key}"
    pinned = getattr(preset, "model", None)
    if pinned:
        return str(pinned), f"preset.{getattr(preset, 'name', '?')}"
    role = configured(workspace, "worker")
    if role:
        return role, "workspace.models.worker"
    return None, "bridge default"


def known_ids(workspace: Any) -> list[str]:
    """Every id the workspace's own ``models:`` block names."""
    if workspace is None:
        return []
    try:
        block = dict(workspace.models or {})
    except AttributeError:  # pragma: no cover
        block = dict(workspace or {})
    return sorted({str(v) for v in block.values() if v})


def unknown_warning(model: str | None, workspace: Any) -> str | None:
    """A warning when ``model`` is not one of the workspace's own ids, else None.

    Hard rule 8 ("Opus for subagents; never Fable") is a budget convention, not
    a correctness invariant, and a literal allowlist of ids in this repo would
    rot within months -- so an unrecognised id is reported and planned, never
    refused.
    """
    if not model:
        return None
    known = known_ids(workspace)
    if not known or model in known:
        return None
    return (f"model {model} is not one of workspace.yaml models: ({', '.join(known)}); "
            "planning anyway -- confirm the id with a 1/1 smoke test before a full fleet "
            "(SKILL.md hard rule 8)")


def tend_agent_model(tend_root: pathlib.Path | str, role: str = "worker") -> str | None:
    """The ``model_name`` in a generated ``.tend/<role>-agent.yaml``.

    ``cf run`` compares this with ``run.yaml``'s ``model`` before it starts, so
    the recorded id is provably the one that ran: run.yaml used to record a
    model the bridge never received (references/method.md: "the yields belong to
    the models that produced them").
    """
    path = pathlib.Path(tend_root) / ".tend" / f"{role}-agent.yaml"
    if not path.exists():
        return None
    found = _MODEL_LINE.search(path.read_text(encoding="utf-8"))
    return found.group(1) if found else None
