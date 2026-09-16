"""YAML loading for adapter configs.

A thin shim over ``cflib.yamlio`` (the core library owns the loader and its
stdlib-only fallback, per the repository conventions).  PyYAML is used as a
second resort so that ``scripts/adapters`` stays importable on its own — the
regression tests import the adapters without going through ``cf.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["load_yaml_text", "load_yaml", "loader_name"]


def _cflib():
    try:
        from cflib import yamlio as _y  # type: ignore
    except Exception:
        return None
    return _y


def loader_name() -> str:
    """Which loader ``load_yaml_text`` will use (diagnostics only)."""
    if _cflib() is not None:
        return "cflib"
    try:
        import yaml  # noqa: F401
    except Exception:
        return "none"
    return "pyyaml"


def load_yaml_text(text: str, where: str = "<yaml>") -> Any:
    """Parse a YAML document."""
    mod = _cflib()
    if mod is not None:
        return mod.load_yaml(text, where=where)
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - only without cflib and PyYAML
        raise RuntimeError(
            f"no YAML loader available for {where}: install PyYAML or run through "
            "scripts/cf.py so that scripts/cflib is importable"
        ) from exc
    return yaml.safe_load(text)


def load_yaml(path: str | Path) -> Any:
    """Parse the YAML file at ``path``."""
    p = Path(path)
    return load_yaml_text(p.read_text(encoding="utf-8"), where=str(p))
