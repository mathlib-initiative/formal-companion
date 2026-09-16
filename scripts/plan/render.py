"""A very small ``{{var}}`` template renderer (no Jinja dependency).

Ported from: the prompt/task/config text that was maintained by hand in
``ffmain:~/tao-diff-tend/prompts/*`` and ``ffmain:~/tao-diff-run/tasks/*.yaml``
(five near-identical copies, which is why they drifted). The renderer exists so
those five copies become one template plus a preset flag.

Supported syntax, deliberately tiny:

* ``{{name}}``            — substitute ``ctx["name"]`` (``str()`` of it).
* ``{{#if name}} … {{/if}}``  — keep the block when ``ctx["name"]`` is truthy.
* ``{{#unless name}} … {{/unless}}`` — keep the block when it is falsy.

A directive that is alone on its line consumes that whole line, so a dropped
block leaves no blank line behind. An unknown ``{{name}}`` raises ``TemplateError`` with
the template name in the message: a prompt with an unfilled slot must never
reach a worker.

``cflib.templates.render`` is deliberately NOT reused: it is the campaign-file
renderer (plain ``{{ key }}`` substitution, no blocks, different missing-slot
semantics), and cflib says so in its own docstring. Prompts need the blocks, so
this is the second and last renderer in the skill.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

__all__ = ["render", "render_file", "TemplateError"]


class TemplateError(ValueError):
    """A template referenced a slot that the caller did not provide."""


_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")
_BLOCK = re.compile(
    r"(?P<open>\{\{#(?P<tag>if|unless)\s+(?P<name>[A-Za-z_][A-Za-z0-9_.]*)\s*\}\})"
    r"(?P<body>.*?)"
    r"(?P<close>\{\{/(?P=tag)\}\})",
    re.DOTALL,
)


def _lookup(ctx: Mapping[str, Any], name: str, where: str) -> Any:
    """Return ``ctx[name]``, allowing dotted access into nested mappings."""
    cur: Any = ctx
    for part in name.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            raise TemplateError(f"{where}: no value for slot {{{{{name}}}}}")
    return cur


def _strip_directive_lines(text: str, *, opened: str, closed: str) -> str:
    """Drop the newline after a directive that sat alone on its own line."""
    for token in (opened, closed):
        text = re.sub(r"(?m)^[ \t]*" + re.escape(token) + r"[ \t]*\n", "", text)
        text = text.replace(token, "")
    return text


def render(template: str, ctx: Mapping[str, Any], *, name: str = "<template>") -> str:
    """Render ``template`` against ``ctx``. Raises ``TemplateError`` on a gap."""

    def _blocks(text: str) -> str:
        while True:
            m = _BLOCK.search(text)
            if m is None:
                return text
            value = _lookup(ctx, m.group("name"), name)
            keep = bool(value) if m.group("tag") == "if" else not value
            body = m.group("body") if keep else ""
            if keep:
                body = _strip_directive_lines(
                    m.group("open") + body + m.group("close"),
                    opened=m.group("open"),
                    closed=m.group("close"),
                )
            start, end = m.span()
            head, tail = text[:start], text[end:]
            # a directive that owned its own line must not leave a blank one behind
            if tail.startswith("\n") and (body == "" or body.endswith("\n")):
                tail = tail[1:]
            text = head + body + tail

    text = _blocks(template)

    def _sub(m: re.Match[str]) -> str:
        return str(_lookup(ctx, m.group(1), name))

    return _VAR.sub(_sub, text)


def render_file(path, ctx: Mapping[str, Any]) -> str:
    """Render the template file at ``path`` (``pathlib.Path`` or ``str``)."""
    import pathlib

    p = pathlib.Path(path)
    return render(p.read_text(encoding="utf-8"), ctx, name=str(p))
