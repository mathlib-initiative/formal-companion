"""`informalize/<id>/informalize.yaml`: what one informalization companion is about.

New code, for docs/proposals/informalize.md section 5.1.  `cf informalize init` builds the
file from its flags and every later stage reads it through :func:`load`, which refuses a
file that breaks a rule of that section rather than reading a default into it.  No host
and no model is named here (workspace.yaml is the only place either is), and the reference
work is a citation line, never a sentence of the work.

`templates/informalize/schema/informalize.schema.json` states the same rules as a JSON
Schema for a reader; this module is the implementation, and
tests/test_informalize_cli.py holds the two to one set of keys and patterns.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cflib.errors import CfError
from cflib.paths import WorkspacePaths
from cflib.yamlio import read_yaml, write_yaml
from overlay.config import OPEN_LICENSES
from plan.make_tend_root import check_agent_timeout

SCHEMA = "formal-companion/informalize"
SCHEMA_VERSION = 1

#: lowercase letters, digits and `-`, a letter or digit first, at most 40 characters, so
#: that `<id>-lib` is a universe id and `<id>-<phase>-<n>` a run id (section 5.1)
ID_PATTERN = r"\A[a-z0-9][a-z0-9-]{0,39}\Z"
COMMIT_PATTERN = r"\A[0-9a-f]{40}\Z"
#: an https URL a LaTeX `\url` and a Markdown link both take as it is
URL_PATTERN = r"\Ahttps://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/-]*)?\Z"
#: a Lean module name: dotted components, each an identifier
MODULE_PATTERN = r"\A[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z"
#: a declaration's full name: dotted components of anything but white space and dots
#: (Lean admits `!`, `?`, subscripts and `«...»`), the last of them not empty
DECLARATION_PATTERN = r"\A[^\s.]+(?:\.[^\s.]+)*\Z"
#: an SPDX license id, as a library's license file is matched to one
SPDX_PATTERN = r"\A[A-Za-z0-9][A-Za-z0-9.+-]{0,63}\Z"
#: one line of text: a title, an author, a citation line
TEXT_LIMIT = 300

TOP_KEYS = ("schema", "schema_version", "id", "library", "text", "reference", "caps")
LIBRARY_KEYS = ("url", "commit", "module", "theorem", "prefix", "license", "license_method",
                "library_cache")
TEXT_KEYS = ("title", "author", "license", "job")
REFERENCE_KEYS = ("work",)
CAP_KEYS = ("workers", "reviewers", "max_cost_per_phase", "agent_timeout")
#: what `init` writes when its flags say nothing: the caps of the first run (section 12),
#: and no spend cap, so the planner refuses a phase until one is given (section 8.1)
DEFAULT_CAPS = {"workers": 6, "reviewers": 4, "max_cost_per_phase": None,
                "agent_timeout": "2h"}
#: `defaults.max_concurrent_sessions` of a workspace that sets none, and the account's
#: hard limit (references/traps.md C3)
SESSION_LIMIT = 16

HEADER = """\
informalize.yaml: one informalization companion (docs/proposals/informalize.md section 5.1).
Written by `cf informalize init`, read by every stage, edited by the maintainer.  No host
and no model is named here: workspace.yaml is the only place either is.  After an edit,
`cf informalize sync <id>` writes informal/informalize.json from it.
"""


def _line(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= TEXT_LIMIT \
        and value == value.strip() and not re.search(r"[\x00-\x1f\x7f]", value)


def library_name(url: str) -> str:
    """The library's name: the last component of its URL, without `.git`."""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".git") else name


def problems(doc: Any, *, sessions: int = SESSION_LIMIT) -> list[str]:
    """Every rule of section 5.1 the document breaks; empty when it keeps them all."""
    if not isinstance(doc, dict):
        return ["informalize.yaml is not a mapping"]
    out: list[str] = []
    out += [f"unknown key {k!r}" for k in doc if k not in TOP_KEYS]
    if doc.get("schema") != SCHEMA:
        out.append(f"schema is not {SCHEMA}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        out.append(f"schema_version is not {SCHEMA_VERSION}")
    iid = doc.get("id")
    if not isinstance(iid, str) or not re.match(ID_PATTERN, iid):
        out.append(f"id {iid!r} is not lowercase letters, digits and -, at most 40 characters")
    out += _library_problems(doc.get("library"))
    out += _text_problems(doc.get("text"))
    reference = doc.get("reference")
    if reference is not None:
        if not isinstance(reference, dict):
            out.append("reference is not a mapping")
        else:
            out += [f"reference: unknown key {k!r}" for k in reference if k not in REFERENCE_KEYS]
            if not _line(reference.get("work")):
                out.append("reference.work is not one line of text")
    out += _cap_problems(doc.get("caps"), sessions)
    return out


def _library_problems(library: Any) -> list[str]:
    if not isinstance(library, dict):
        return ["library is not a mapping"]
    out = [f"library: unknown key {k!r}" for k in library if k not in LIBRARY_KEYS]
    url = library.get("url")
    if not isinstance(url, str) or not re.match(URL_PATTERN, url):
        out.append(f"library.url {url!r} is not an https URL")
    if not isinstance(library.get("commit"), str) or not re.match(COMMIT_PATTERN, library["commit"]):
        out.append(f"library.commit {library.get('commit')!r} is not 40 hex")
    module, prefix = library.get("module"), library.get("prefix")
    for key, value in (("module", module), ("prefix", prefix)):
        if not isinstance(value, str) or not re.match(MODULE_PATTERN, value):
            out.append(f"library.{key} {value!r} is not a Lean module name")
    if isinstance(module, str) and isinstance(prefix, str) \
            and module != prefix and not module.startswith(prefix + "."):
        out.append(f"library.module {module} is not under library.prefix {prefix}")
    theorem = library.get("theorem")
    if not isinstance(theorem, str) or not re.match(DECLARATION_PATTERN, theorem):
        out.append(f"library.theorem {theorem!r} is not a declaration's full name")
    license_ = library.get("license")
    if license_ is not None and (not isinstance(license_, str) or not re.match(SPDX_PATTERN, license_)):
        out.append(f"library.license {license_!r} is not an SPDX id")
    method = library.get("license_method")
    if method is not None and method != "manual":
        out.append(f"library.license_method {method!r} is not `manual`")
    if method == "manual" and license_ is None:
        out.append("library.license_method is manual but library.license is null")
    if not isinstance(library.get("library_cache"), bool):
        out.append("library.library_cache is not true or false")
    return out


def _text_problems(text: Any) -> list[str]:
    if not isinstance(text, dict):
        return ["text is not a mapping"]
    out = [f"text: unknown key {k!r}" for k in text if k not in TEXT_KEYS]
    for key in ("title", "author"):
        if not _line(text.get(key)):
            out.append(f"text.{key} is not one line of text")
    if text.get("license") not in OPEN_LICENSES:
        out.append(f"text.license {text.get('license')!r} is not one of {', '.join(OPEN_LICENSES)}")
    job = text.get("job")
    if not isinstance(job, str) or not re.match(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z", job):
        out.append(f"text.job {job!r} is not a plain file name")
    return out


def _cap_problems(caps: Any, sessions: int) -> list[str]:
    if not isinstance(caps, dict):
        return ["caps is not a mapping"]
    out = [f"caps: unknown key {k!r}" for k in caps if k not in CAP_KEYS]
    counts = {}
    for key in ("workers", "reviewers"):
        value = caps.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            out.append(f"caps.{key} {value!r} is not a positive integer")
        else:
            counts[key] = value
    if len(counts) == 2 and sum(counts.values()) > sessions:
        out.append(f"caps.workers + caps.reviewers is {sum(counts.values())}, over the "
                   f"{sessions} concurrent sessions the workspace allows")
    cost = caps.get("max_cost_per_phase")
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost <= 0):
        out.append(f"caps.max_cost_per_phase {cost!r} is not a positive number of dollars")
    timeout = caps.get("agent_timeout")
    if timeout is not None:
        try:
            check_agent_timeout(str(timeout))
        except CfError as exc:
            out.append(f"caps.agent_timeout: {exc.message}")
    return out


def build(*, iid: str, url: str, commit: str, module: str, theorem: str, prefix: str,
          text_license: str, title: str, author: str, reference: str | None = None,
          library_license: str | None = None, library_cache: bool = False,
          workers: int | None = None, reviewers: int | None = None,
          max_cost_per_phase: float | None = None, agent_timeout: str | None = None) -> dict:
    """The document `init` writes, in section 5.1's order."""
    library: dict[str, Any] = {"url": url, "commit": commit, "module": module,
                               "theorem": theorem, "prefix": prefix, "license": library_license}
    if library_license is not None:
        library["license_method"] = "manual"
    library["library_cache"] = bool(library_cache)
    caps = dict(DEFAULT_CAPS)
    for key, value in (("workers", workers), ("reviewers", reviewers),
                       ("max_cost_per_phase", max_cost_per_phase),
                       ("agent_timeout", agent_timeout)):
        if value is not None:
            caps[key] = value
    doc: dict[str, Any] = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "id": iid, "library": library,
        "text": {"title": title, "author": author, "license": text_license, "job": iid},
    }
    if reference is not None:
        doc["reference"] = {"work": reference}
    doc["caps"] = caps
    return doc


def write(path: Path, doc: dict) -> Path:
    found = problems(doc)
    if found:
        raise CfError(f"refusing to write {path}: {found[0]}", hint="; ".join(found[1:4]) or None)
    return write_yaml(path, doc, header="".join(f"# {line}\n" if line else "#\n"
                                                 for line in HEADER.splitlines()))


def read(path: Path) -> Any:
    try:
        return read_yaml(path)
    except FileNotFoundError:
        return None


def load(paths: WorkspacePaths, informalize_id: str, *, sessions: int = SESSION_LIMIT) -> dict:
    """The settings of one informalize id, or a refusal naming what is wrong."""
    path = paths.informalize_yaml(informalize_id)
    if not path.is_file():
        raise CfError(f"no {path}", hint=f"`cf informalize init {informalize_id} ...` writes it")
    try:
        doc = read_yaml(path)
    except CfError as exc:
        raise CfError(exc.message, hint="edit it, or re-run `cf informalize init` with --force, "
                                        "which replaces a file that does not parse") from None
    found = problems(doc, sessions=sessions)
    if not found and doc.get("id") != informalize_id:
        found = [f"id {doc.get('id')!r} is not {informalize_id!r}, the directory it is in"]
    if found:
        raise CfError(f"{path}: {found[0]}", hint="; ".join(found[1:4]) or
                      "edit it, or re-run `cf informalize init` with --force")
    return doc


def session_limit(workspace) -> int:
    """`defaults.max_concurrent_sessions` of the workspace, else the account's limit."""
    defaults = getattr(workspace, "defaults", None) or {}
    value = defaults.get("max_concurrent_sessions") if isinstance(defaults, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 \
        else SESSION_LIMIT
