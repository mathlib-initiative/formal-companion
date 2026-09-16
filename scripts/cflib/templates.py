"""Rendering for the skill's own templates (campaign.yaml, adapter.yaml, ...).

New code.  A deliberately tiny ``{{ key }}`` substitution: the campaign files
must keep their comments, which a YAML dump would destroy, and the skill must
not depend on Jinja for its core commands.  (plan/render_prompts.py may use a
richer renderer for prompt templates.)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from .errors import CfError
from .paths import templates_dir

_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


def render(text: str, values: Mapping[str, object], allow_missing: bool = False) -> str:
    """Replace ``{{ key }}`` tokens. Unknown tokens raise unless allowed."""
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in values:
            val = values[key]
            return "" if val is None else str(val)
        missing.append(key)
        return m.group(0)

    out = _TOKEN.sub(sub, text)
    if missing and not allow_missing:
        raise CfError(f"template placeholders not provided: {', '.join(sorted(set(missing)))}")
    return out


def render_template(name: str, values: Mapping[str, object], allow_missing: bool = False) -> str:
    """Render ``templates/<name>`` from the skill repo."""
    path = templates_dir() / name
    if not path.exists():
        raise CfError(f"missing template: {path}")
    return render(path.read_text(encoding="utf-8"), values, allow_missing=allow_missing)


def copy_template(name: str, dest: str | Path, values: Mapping[str, object] | None = None,
                  overwrite: bool = False) -> tuple[Path, bool]:
    """Render a template to ``dest``. Returns (path, written).

    Never overwrites an existing file unless asked: conventions.md is edited by
    hand after onboarding and must survive a re-run.
    """
    from .jsonio import atomic_write

    p = Path(dest)
    if p.exists() and not overwrite:
        return p, False
    atomic_write(p, render_template(name, values or {}, allow_missing=True))
    return p, True
