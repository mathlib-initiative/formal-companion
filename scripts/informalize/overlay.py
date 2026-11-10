"""The annotations, the overlay and the built site, stage I7: `cf informalize overlay` and
`check-site`.

New code, for docs/proposals/informalize.md section 10.  Both run on the operator's
machine, in the output repository, and nowhere else:

* **overlay** refuses without a current proof-sheets approval, then does in order what the
  pilot did by hand: it writes `pdf-overlay/overlay.config.json` through
  `overlay.sync.init` and sets the keys an informalization companion always has (the
  text's license under `pdf.publish`, `site.output_root` `site`, no build command, the
  library's prefix as `display.strip_prefix`, `display.minimal`, and the text's title as
  the title page prints it rather than as `main.tex` sets it,
  `config.printed_title`), renders the landing page
  `site/index.html` -- with the overlay link block already in it, so the build's injection
  replaces it in place and leaves the tracked file as it is -- and the two workflows, and
  commits them, with the `pdf-overlay/verso.json` the maintainer rsynced back from the
  Verso host when there is one: that is the one file of the overlay the companion owns,
  and step 2 reads the repository at a commit.  Then it exports with
  `cf export informal-markers` through `overlay.export.export`,
  syncs and checks the overlay, and commits; and seeds the PDF cache with the canonical
  PDF, so the site builds before the release URL is live.  Nothing here is edited by hand,
  so a second run writes the same bytes and makes no commit.
* **check-site** runs the companion's own `check_generated.py` and
  `pdf-overlay/tools/serve.py --build-only`, then reads the built site against its
  receipt: every file at its sha256, the published PDF at the sha256 the annotations
  state, every region on a page of it, the index link, and a card for every annotation
  with a declaration and a destination.  It writes the I7 receipt, which
  `cf informalize approve <id> publish` pins.

Publishing itself -- the repository, the release asset, Pages -- is the maintainer's
(section 13).  Neither command reaches the network: the PDF is the one `fetch-pdf`
brought home, and `serve.py` reuses it from the cache.
"""
from __future__ import annotations

import getpass
import html
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cflib.errors import CfError
from cflib.hashing import sha256_file
from cflib.paths import templates_dir
from cflib.templates import render
from overlay import config as overlay_config
from overlay import export as overlay_export
from overlay import shards as overlay_shards
from overlay import sync as overlay_sync

from informalize import approve as gates
from informalize import config, pdf as pdf_mod, project, stages
from informalize.outrepo import OutputRepo

#: what the two commands write and read in the output repository
CONFIG = f"{overlay_sync.OVERLAY_DIRNAME}/{overlay_config.CONFIG_NAME}"
GENERATED = f"{overlay_sync.OVERLAY_DIRNAME}/{overlay_sync.GENERATED_NAME}"
BUNDLE = f"{overlay_sync.OVERLAY_DIRNAME}/{overlay_sync.BUNDLE_NAME}"
VERSO = f"{overlay_sync.OVERLAY_DIRNAME}/verso.json"
SITE_INDEX = "site/index.html"
WORKFLOWS = ".github/workflows"
GITIGNORE = ".gitignore"
#: the convention `cf export` writes an informalization companion's document with
CONVENTION = "informal-markers"
#: the site's settings, which are the same for every informalization companion
OUTPUT_ROOT = "site"
INDEX_NAME = "index.html"
LINK_TEXT = "Open the annotated PDF"
#: the tools the companion runs, under its own `pdf-overlay/tools/`
CHECK_TOOL = f"{overlay_sync.OVERLAY_DIRNAME}/tools/check_generated.py"
SERVE_TOOL = f"{overlay_sync.OVERLAY_DIRNAME}/tools/serve.py"
BUILD_TOOL = "build_site.py"
#: what `build_site.py` writes beside the mounted runtime
SITE_RECEIPT = "receipt.json"
#: the .gitignore lines the site and the PDF cache need, in order
IGNORE_LINES = ("*.pdf", ".lake/", "/build/", "/site/*", "!/site/index.html", "__pycache__/")
#: how long the two tools may run
TIMEOUT = 900


def site_templates() -> Path:
    """`templates/informalize/site/`: the landing page and the two workflows."""
    return templates_dir() / "informalize" / "site"


def build_site_tool():
    """`templates/overlay/runtime/tools/build_site.py`, imported from this checkout.

    The landing page carries the overlay link block the build would inject, so that the
    build replaces it in place and the tracked `site/index.html` does not change; the
    block's bytes therefore come from the tool that writes them, never from a copy here
    (`overlay.sync._verso_problems` imports `check_generated.py` the same way).  It is
    put in `sys.modules` before it is executed, as any import does: its own dataclasses
    resolve their module there.
    """
    name = "overlay_build_site"
    if name in sys.modules:
        return sys.modules[name]
    path = overlay_sync.runtime_root() / "tools" / BUILD_TOOL
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:                  # pragma: no cover
        raise CfError(f"{path} cannot be imported", hint="this is not a formal-companion "
                                                         "checkout")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:                                    # pragma: no cover
        sys.modules.pop(name, None)
        raise
    return module


# ---------------------------------------------------------------------------
# step 1: the config, the landing page, the workflows
# ---------------------------------------------------------------------------

def viewer_href(mount: str = overlay_sync.OVERLAY_DIRNAME, index: str = INDEX_NAME) -> str:
    """The viewer's href as the site index spells it."""
    return build_site_tool().link_href(SimpleNamespace(mount=mount, index=index))


def config_value(cfg: dict, repo: OutputRepo) -> dict:
    """`overlay.config.json` for this informalization companion, every key settled here."""
    job = cfg["text"]["job"]
    overlay_sync.init(repo.root, source_id=cfg["id"], title=config.printed_title(cfg),
                      file_name=f"{job}.pdf", force=True)
    value = json.loads((repo.root / CONFIG).read_text(encoding="utf-8"))
    name = config.library_name(cfg["library"]["url"])
    value["lede"] = f"Each statement of the text, and the {name} declaration that states it."
    value.pop("back_link", None)
    value["display"] = {"strip_prefix": f"{cfg['library']['prefix']}.", "minimal": True}
    value["pdf"]["publish"] = {"license": cfg["text"]["license"]}
    value["site"] = {"output_root": OUTPUT_ROOT, "index": INDEX_NAME,
                     "mount": overlay_sync.OVERLAY_DIRNAME, "link_text": LINK_TEXT}
    value["build"] = {"commands": []}
    overlay_config.parse(value)            # never write a config `cf overlay check` refuses
    return value


def index_html(cfg: dict) -> bytes:
    """The landing page, with the overlay link block the build writes, so it writes it back."""
    tool = build_site_tool()
    values = {
        "title": html.escape(config.printed_title(cfg)),
        "viewer_href": html.escape(viewer_href(), quote=True),
        "link_block": tool.link_block(viewer_href(), LINK_TEXT, ""),
    }
    text = render((site_templates() / "index.html").read_text(encoding="utf-8"), values)
    return text.encode("utf-8")


def workflow_files() -> dict[str, bytes]:
    """The two workflows, byte for byte: a Pages artifact build and the generated check."""
    root = site_templates() / "workflows"
    return {f"{WORKFLOWS}/{path.name}": path.read_bytes()
            for path in sorted(root.glob("*.yml"))}


def ignore_bytes(current: bytes | None) -> bytes:
    """`.gitignore` with every line the site and the PDF cache need, the rest kept."""
    lines = (current or b"").decode("utf-8", "replace").splitlines()
    missing = [line for line in IGNORE_LINES if line not in lines]
    if not missing:
        return current if current is not None else b""
    return ("\n".join(lines + missing).strip("\n") + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# step 2: the export, and the one value in it that would never settle
# ---------------------------------------------------------------------------

def keep_export_commit(before: bytes | None, after: bytes) -> bytes | None:
    """`before` again, when the export's own commit is the only thing that changed in it.

    `GENERATED.json`'s `annotations.export.commit` is the output repository's HEAD when
    the export read it -- and committing the record moves HEAD, so the next run reads a
    different one.  Left alone, every run would write that one new value and commit it:
    the command would never converge, and the publish approval, which pins this file,
    would go stale each time (CLAUDE.md section 4, idempotence).

    What the annotations are of is the `informal/` the export read, and
    `annotations.inputs` carries the sha256 of each of those four files, so any change to
    them shows up here as well.  A record that differs ONLY in the commit therefore
    describes the same bytes, and the commit already in it names them as truly as the new
    one: it is kept, and nothing is committed.  Anything more differs and `None` says so.
    """
    if before is None:
        return None
    try:
        old, new = json.loads(before), json.loads(after)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    export = (new.get("annotations") or {}).get("export")
    kept = ((old.get("annotations") or {}).get("export") or {}).get("commit")
    if not isinstance(export, dict) or not isinstance(kept, str) or export.get("commit") == kept:
        return None
    export["commit"] = kept                    # `new` is this call's own, read just above
    return before if new == old else None


# ---------------------------------------------------------------------------
# the PDF the annotations pin
# ---------------------------------------------------------------------------

def pdf_source(repo: OutputRepo, source_id: str) -> dict:
    """The PDF end of the exported annotations: its sha256, bytes and pages."""
    for record in overlay_sync.read_annotations(repo.root).records.values():
        for end in record.get("subject") or []:
            if not isinstance(end, dict) or end.get("type") != "pdf":
                continue
            extension = (end.get("extensions") or {}).get(overlay_shards.NAMESPACE) or {}
            if extension.get("source_id") != source_id:
                continue
            return {"sha256": end.get("sha256"), "bytes": extension.get("bytes"),
                    "pages": extension.get("physical_page_count"),
                    "uri": end.get("uri")}
    raise CfError(f"no exported annotation is a record of the PDF {source_id!r}",
                  hint="`cf informalize overlay` exports them; `cf overlay check` says what "
                       "is wrong with the ones on disk")


# ---------------------------------------------------------------------------
# cf informalize overlay
# ---------------------------------------------------------------------------

@dataclass
class OverlayResult:
    """What `cf informalize overlay` wrote."""

    head: str
    pdf_url: str
    written: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cache: Path | None = None


def _tracked_under(repo: OutputRepo, prefixes: tuple[str, ...]) -> list[str]:
    """Every path under `prefixes` that is on disk or in the index: the commit's path list.

    A file an export deleted is in the index and not on disk, and must be committed as a
    deletion; `pdf-overlay/verso.json`, the one file of the overlay the companion owns
    (`docs/overlay.md` section 5), is under the prefix like any other, and step 1 has
    already committed it.
    """
    out = {path for path in repo.tracked()
           if any(path.startswith(prefix + "/") for prefix in prefixes)}
    for prefix in prefixes:
        root = repo.root / prefix
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                out.add(path.relative_to(repo.root).as_posix())
    return sorted(out)


def overlay(workspace, informalize_id: str, *, pdf_url: str,
            packages_src: Path | None = None) -> OverlayResult:
    """Stage I7's three steps, in order, in the output repository."""
    paths = workspace.paths
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    if not re.fullmatch(r"https://[^\s<>\"]+", pdf_url or ""):
        raise CfError(f"--pdf-url {pdf_url!r} is not an https URL",
                      hint="it is where the maintainer publishes the canonical PDF's exact "
                           "bytes (a release asset); the viewer fetches it and verifies its "
                           "sha256")
    repo = OutputRepo(paths, informalize_id)
    repo.require_stamp()
    pdf_mod.require_clean(repo, "this command commits what it writes, and the export reads "
                                "the repository at a commit", allow=(VERSO,))
    state, stale = gates.gate_state(workspace, informalize_id, "proof-sheets")
    if state != "current":
        raise CfError(f"the proof-sheets approval is {state}"
                      + (f" ({stale[0]})" if stale else ""),
                      hint=f"`cf informalize approve {informalize_id} proof-sheets --by "
                           "<name>` after reading the proof sheets and the PDF; the "
                           "annotations pin the PDF it signs")
    receipt = pdf_mod.current_receipt(paths, informalize_id)
    job = cfg["text"]["job"]
    canonical = paths.informalize_pdf(informalize_id, receipt["checks"]["source_commit"][:12]) \
        / f"{job}.pdf"
    library_src = paths.informalize_library_src(informalize_id)
    if not library_src.is_dir():
        raise CfError(f"no {library_src}: stage I1 has not run",
                      hint=f"`cf informalize fetch-library {informalize_id}` unpacks the "
                           "library's source, which the cards' snippets are cut from")
    result = OverlayResult(head="", pdf_url=pdf_url)

    # 1. the config, the landing page, the workflows and the .gitignore lines.  Everything
    # is rendered before anything is written: `overlay.sync.init` writes the config itself,
    # and a failure after it would leave the repository dirty for the next run to refuse.
    files = {**project.missing_repository_files(repo, cfg),
             SITE_INDEX: index_html(cfg), **workflow_files(),
             GITIGNORE: ignore_bytes(repo.read_bytes(GITIGNORE))}
    value = config_value(cfg, repo)
    files[CONFIG] = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    for relative, raw in files.items():
        if repo.write(relative, raw):
            result.written.append(relative)
    # `pdf-overlay/verso.json` is the one file of the overlay the companion owns
    # (`docs/overlay.md` section 5): the maintainer rsyncs it back from the Verso host, and
    # it is committed here, with the files this step writes, because step 2 reads the
    # repository at a commit and refuses a tree with anything uncommitted in it.  What is
    # wrong with one is `cf overlay check`'s answer, below, not this step's.
    hovers = bool(repo.changes([VERSO]))
    made = repo.commit(sorted(files) + ([VERSO] if hovers else []),
                       "Configure the overlay, the landing page, the workflows and the "
                       "hovers (I7)" if hovers else
                       "Configure the overlay, the landing page and the workflows (I7)")
    if made:
        result.commits.append(made)
    head = repo.head()
    result.head = head

    # 2. the annotations, the overlay runtime and the bundle
    approved = gates.gate_state(workspace, informalize_id, "items")[0] == "current"
    extra = ["--pdf", str(canonical), "--pdf-url", pdf_url, "--library-src", str(library_src),
             "--source-title", config.printed_title(cfg)]
    if packages_src is not None:
        extra += ["--packages-src", str(packages_src)]
    if approved:
        extra.append("--items-approved")
    recorded = repo.read_bytes(GENERATED)
    exported = overlay_export.export(repo.root, convention=CONVENTION, commit=head,
                                     source_id=informalize_id, extra=extra)
    overlay_sync.sync(repo.root)
    kept = keep_export_commit(recorded, repo.read_bytes(GENERATED) or b"")
    if kept is not None:
        repo.write(GENERATED, kept)            # the same export: let the run converge
    problems = overlay_sync.check(repo.root)
    if problems:
        raise CfError(f"the overlay does not check out: {problems[0]}",
                      hint="; ".join(problems[1:5]) or
                           f"`cf overlay check {repo.root}` prints them all")
    paths_2 = _tracked_under(repo, (overlay_sync.OVERLAY_DIRNAME,
                                    overlay_sync.ANNOTATIONS_DIRNAME))
    made = repo.commit(paths_2, f"Export the overlay's annotations from the canonical PDF "
                                f"(I7): {sum(exported.sections.values())} files")
    if made:
        result.commits.append(made)
    result.counts = dict(exported.counts)
    result.counts["annotation_files"] = sum(exported.sections.values())
    result.counts["sections"] = len(exported.sections)
    result.warnings = list(exported.warnings)
    result.head = repo.head()

    # 3. the PDF cache, so the site builds before the release URL is live
    source = pdf_source(repo, informalize_id)
    if source["sha256"] != sha256_file(canonical) \
            or source["bytes"] != canonical.stat().st_size:
        raise CfError("the exported annotations do not pin the canonical PDF that is here",
                      hint="re-run `cf informalize fetch-pdf` and this command")
    cache = repo.target(f"{value['pdf']['cache_dir']}/{value['pdf']['file_name']}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(canonical, cache)
    result.cache = cache
    return result


# ---------------------------------------------------------------------------
# cf informalize check-site
# ---------------------------------------------------------------------------

@dataclass
class SiteResult:
    """What `cf informalize check-site` read."""

    head: str
    checks: dict = field(default_factory=dict)
    receipt: Path | None = None


def _tool(repo: OutputRepo, relative: str, *args: str) -> subprocess.CompletedProcess:
    path = repo.root / relative
    if path.is_symlink() or not path.is_file():
        raise CfError(f"the output repository has no {relative}",
                      hint=f"`cf informalize overlay {repo.id} --pdf-url <url>` writes the "
                           "overlay runtime")
    return subprocess.run([sys.executable, str(path), *args], cwd=str(repo.root),
                          capture_output=True, text=True, timeout=TIMEOUT)


def check_site(workspace, informalize_id: str) -> SiteResult:
    """Run the companion's own checks and the site build, read what they wrote, write I7."""
    paths = workspace.paths
    cfg = config.load(paths, informalize_id, sessions=config.session_limit(workspace))
    repo = OutputRepo(paths, informalize_id)
    repo.require_stamp()
    pdf_mod.require_clean(repo, "the site is built from the repository as it stands, and the "
                                "publish approval pins a commit")
    receipt = pdf_mod.current_receipt(paths, informalize_id)
    config_path = repo.root / CONFIG
    if not config_path.is_file():
        raise CfError(f"the output repository has no {CONFIG}",
                      hint=f"`cf informalize overlay {informalize_id} --pdf-url <url>` first")
    settings = overlay_config.load(config_path)
    checks: dict = {"head": repo.head()}

    generated = _tool(repo, CHECK_TOOL)
    if generated.returncode != 0:
        raise CfError(f"{CHECK_TOOL} exited {generated.returncode}",
                      hint=((generated.stdout or "") + (generated.stderr or "")).strip()[-600:])
    checks["check_generated"] = "ok"
    built = _tool(repo, SERVE_TOOL, "--build-only")
    if built.returncode != 0:
        raise CfError(f"{SERVE_TOOL} --build-only exited {built.returncode}",
                      hint=((built.stdout or "") + (built.stderr or "")).strip()[-600:])
    checks["build_only"] = "ok"
    changed = repo.changes()
    if changed:
        raise CfError(f"the site build changed {len(changed)} tracked file(s), the first "
                      f"{changed[0]}",
                      hint="the landing page carries the overlay link block already, so the "
                           "build writes it back unchanged; commit the difference and say "
                           "why, or re-run `cf informalize overlay`")
    checks["repository_clean_after_build"] = True

    site = repo.root / settings.output_root
    mount = site.joinpath(*PurePosixPath(settings.mount).parts)
    built_receipt = _read(mount / SITE_RECEIPT, f"{settings.output_root}/{settings.mount}/"
                                                f"{SITE_RECEIPT}")
    problems = _site_problems(repo, settings, site, mount, built_receipt, receipt)
    if problems:
        raise CfError(f"the built site fails {len(problems)} check(s): {problems[0]}",
                      hint="; ".join(problems[1:5]) or None)
    checks["site_files"] = len(built_receipt.get("files") or [])
    checks["annotation_files"] = len((built_receipt.get("annotations") or {}).get("files") or [])
    checks["index"] = {k: (built_receipt.get("index") or {}).get(k)
                       for k in ("href", "marker", "path", "sha256")}
    checks["verso"] = (built_receipt.get("verso") or {}).get("source")
    cards = _card_problems(repo)
    if cards:
        raise CfError(f"{len(cards)} card(s) carry no declaration or no destination: "
                      f"{cards[0]}", hint="; ".join(cards[1:5]) or None)
    source = pdf_source(repo, informalize_id)
    checks["pdf"] = {"sha256": source["sha256"], "bytes": source["bytes"],
                     "pages": source["pages"], "url": source["uri"],
                     "published_as": f"{settings.mount}/{settings.file_name}"}
    checks["license"] = settings.publish_license
    checks["operator"] = getpass.getuser()
    written = _write_receipt(paths, informalize_id, repo, cfg, checks, receipt)
    return SiteResult(head=repo.head(), checks=checks, receipt=written)


def _read(path: Path, what: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise CfError(f"the built site has no {what}",
                      hint="`python3 pdf-overlay/tools/serve.py --build-only` writes it")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CfError(f"{what} is not JSON: {exc}") from None
    if not isinstance(value, dict):
        raise CfError(f"{what} is not an object")
    return value


def _site_problems(repo: OutputRepo, settings, site: Path, mount: Path, built: dict,
                   pdf_receipt: dict) -> list[str]:
    """Every file the build recorded, the published PDF, the regions and the index link."""
    out: list[str] = []
    for row in built.get("files") or []:
        out += _file_problem(mount, row, settings.mount)
    directory = (built.get("annotations") or {}).get("directory") or \
        overlay_sync.ANNOTATIONS_DIRNAME
    root = site.joinpath(*PurePosixPath(directory).parts)
    for row in (built.get("annotations") or {}).get("files") or []:
        out += _file_problem(root, row, directory)
    index = built.get("index") or {}
    path = site.joinpath(*PurePosixPath(str(index.get("path") or "")).parts)
    if not path.is_file():
        out.append(f"the built site has no {index.get('path')!r}")
    elif sha256_file(path) != index.get("sha256"):
        out.append(f"{index.get('path')} is not the index the build recorded")
    elif str(index.get("marker") or "") not in path.read_text(encoding="utf-8", errors="replace"):
        out.append(f"{index.get('path')} carries no overlay link marker")
    if index.get("href") != viewer_href(settings.mount, settings.index):
        out.append(f"the index links to {index.get('href')!r}, not the viewer")

    published = mount / settings.file_name
    if not settings.publish_license:
        out.append(f"{CONFIG} publishes no PDF: pdf.publish.license names no open license")
    elif not published.is_file():
        out.append(f"the built site has no {settings.mount}/{settings.file_name}")
    else:
        stated = pdf_receipt["checks"]["pdf"]
        if sha256_file(published) != stated["sha256"]:
            out.append(f"{settings.mount}/{settings.file_name} is not the canonical PDF the "
                       "I6 receipt pins")
        elif published.stat().st_size != stated["bytes"]:
            out.append(f"{settings.mount}/{settings.file_name} is {published.stat().st_size} "
                       f"bytes, not the canonical PDF's {stated['bytes']}")
    out += _region_problems(repo, pdf_receipt["checks"]["pdf"].get("pages"))
    return out


def _file_problem(root: Path, row: object, label: str) -> list[str]:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        return [f"{label}: the build's receipt lists something that is not a file"]
    pure = PurePosixPath(row["path"])
    if pure.is_absolute() or ".." in pure.parts:
        return [f"{label}/{row['path']}: not a path inside the site"]
    path = root.joinpath(*pure.parts)
    if path.is_symlink() or not path.is_file():
        return [f"{label}/{row['path']}: the build recorded it and the site has not got it"]
    if sha256_file(path) != row.get("sha256"):
        return [f"{label}/{row['path']}: not the bytes the build recorded"]
    return []


def _region_problems(repo: OutputRepo, pages: object) -> list[str]:
    """Every annotation's region is on a page of the PDF, inside it."""
    out: list[str] = []
    for path, record in overlay_sync.read_annotations(repo.root).records.items():
        for end in record.get("subject") or []:
            if not isinstance(end, dict) or end.get("type") != "pdf":
                continue
            location = end.get("location") if isinstance(end.get("location"), dict) else {}
            page, rect = location.get("page"), location.get("rect")
            if not isinstance(page, int) or isinstance(page, bool) or page < 1 \
                    or (isinstance(pages, int) and page > pages):
                out.append(f"{path}: {record.get('id')!r} is on page {page!r}, which the PDF "
                           f"has not got ({pages} pages)")
                continue
            if not (isinstance(rect, list) and len(rect) == 4
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                            for v in rect)):
                out.append(f"{path}: {record.get('id')!r} has no rectangle")
                continue
            x, y, w, h = (float(v) for v in rect)
            if min(x, y, w, h) < 0 or x + w > 1.0000001 or y + h > 1.0000001:
                out.append(f"{path}: {record.get('id')!r} has a region off the page: {rect}")
    return out


def _card_problems(repo: OutputRepo) -> list[str]:
    """Every card carries a declaration and a destination (section 10)."""
    out: list[str] = []
    bundle = repo.read_bytes(BUNDLE)
    records = overlay_sync.bundle_records(bundle) if bundle is not None else None
    if records is None:
        return [f"{BUNDLE} is not a readable annotation bundle; run `cf overlay sync`"]
    for path, record in sorted(records.items()):
        ends = [end for end in record.get("object") or [] if isinstance(end, dict)]
        if not ends:
            out.append(f"{path}: {record.get('id')!r} links to no declaration")
            continue
        for end in ends:
            extension = (end.get("extensions") or {}).get(overlay_shards.NAMESPACE) or {}
            name = (end.get("location") or {}).get("declaration")
            href = (extension.get("destination") or {}).get("href")
            if not isinstance(name, str) or not name:
                out.append(f"{path}: {record.get('id')!r} has an end that names no declaration")
            if not isinstance(href, str) or not href:
                out.append(f"{path}: {record.get('id')!r}: {name} has no destination")
    return out


def _write_receipt(paths, informalize_id: str, repo: OutputRepo, cfg: dict, checks: dict,
                   pdf_receipt: dict) -> Path:
    """The I7 receipt: what `approve <id> publish` pins, with the stage before it."""
    home = paths.informalize_dir(informalize_id)
    inputs = {f"repo/informal/{name}": sha256_file(repo.root / "informal" / name)
              for name in ("informalize.json", "items.json", "declarations.json",
                           "markers.json")}
    inputs["stages/I6.json"] = sha256_file(paths.informalize_receipt(informalize_id, "I6"))
    outputs = {}
    for relative in (CONFIG, GENERATED, BUNDLE, SITE_INDEX, VERSO,
                     *sorted(workflow_files()), *sorted(project.repository_files(cfg))):
        path = repo.root / relative
        if path.is_file() and not path.is_symlink():
            outputs[f"repo/{relative}"] = sha256_file(path)
    checks = dict(checks, source_commit=pdf_receipt["checks"]["source_commit"],
                  text_license=cfg["text"]["license"])
    return stages.write_receipt(paths, informalize_id, "I7", inputs=inputs, outputs=outputs,
                                checks=checks)
