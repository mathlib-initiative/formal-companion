"""``cf corpus`` -- universes of Lean libraries, their indexes, and ``corpus.sqlite``.

New code for docs/proposals/wikipedia-locate.md sections 2.3-2.5 and 6, over
the files references/locate-documents.md section 8 fixes::

    cf corpus init u1 [--frozen 2026-10-17]
    cf corpus add u1 --library alpha --repo https://example.org/alpha --commit <40 hex> \\
        --toolchain leanprover/lean4:v4.34.0 --roots Alpha [--brain-lib Alpha] [--licence Apache-2.0]
    cf corpus add u1 --library gamma --exclude "the build failed at the pin"
    cf corpus probe-command u1 --library alpha          # prints the host pipeline
    cf corpus probe-command u1 --canary --toolchain leanprover/lean4:v4.34.0
    cf corpus ingest u1 --canary --from <canary index dir>
    cf corpus ingest u1 --library alpha --from <index dir> [--checkout <clean checkout at the pin>]
    cf corpus ingest u1 --library gamma --from <published index dir> --accept-published "<why>"
    cf corpus build-db u1
    cf corpus status u1 [--json]
    cf corpus search u1 spanning gadget [--library alpha] [--limit 20]
    cf corpus show u1 alpha Widget.exists_spanning_gadget

``cf`` never builds a library (the rule ``detect-proxies --lean`` follows):
``probe-command`` PRINTS the per-library pipeline for the operator to run on
the host, and ``ingest`` reads the probe's output when it has come back.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path

from cflib import log
from cflib.errors import CfError
from cflib.hashing import short
from cflib.log import format_table as _format_table
from cflib.workspace import from_args

from corpus import build_db, ingest as ingest_mod, query
from corpus.universe import TOOLCHAIN, Universe, lean_version

#: the host drivers probe-command names, beside each other on the host as here
DRIVERS = ("scripts/corpus/build_universe_host.sh", "scripts/corpus/probe_host.sh",
           "scripts/corpus/probe_merge.py", "scripts/corpus/universe_host.py",
           "templates/corpus/probe.lean.tmpl")
DEFAULT_MIN_FREE_GB = 450


def register(subparsers) -> None:
    p = subparsers.add_parser("corpus", help="universes of Lean libraries: indexes and corpus.sqlite")
    sub = p.add_subparsers(dest="corpus_command", metavar="<subcommand>")

    init = sub.add_parser("init", help="create corpora/<universe>/universe.yaml")
    init.add_argument("universe")
    init.add_argument("--frozen", default=None, help="the date the set is fixed (default: today)")

    add = sub.add_parser("add", help="pin one library in a universe, or exclude it with a reason")
    add.add_argument("universe")
    add.add_argument("--library", required=True, help="the universe-local id (a document's `library`)")
    add.add_argument("--repo", help="https repository URL")
    add.add_argument("--commit", help="the 40-hex commit")
    add.add_argument("--toolchain", help="the lean-toolchain, e.g. leanprover/lean4:v4.34.0")
    add.add_argument("--roots", nargs="+", default=None, help="root modules")
    add.add_argument("--brain-lib", default=None, help="the consumer's library token (default: the id)")
    add.add_argument("--licence", default=None, help="SPDX licence id")
    add.add_argument("--conventions", default=None, metavar="FILE",
                     help="the library's conventions file, copied to conventions/<library>.md")
    add.add_argument("--exclude", default=None, metavar="REASON",
                     help="record the library as excluded (failed to build or probe), with why")

    probe = sub.add_parser("probe-command", help="print the host pipeline for one library (runs nothing)")
    probe.add_argument("universe")
    probe.add_argument("--library", default=None)
    probe.add_argument("--canary", action="store_true", help="the acceptance canary of a toolchain")
    probe.add_argument("--toolchain", default=None, help="with --canary: the toolchain to check")
    probe.add_argument("--atlas-root", default=None,
                       help="the host directory the drivers work in (default: $ATLAS_ROOT, "
                            "else $HOME/atlas, as the driver itself defaults)")
    probe.add_argument("--min-free-gb", type=int, default=DEFAULT_MIN_FREE_GB,
                       help=f"the disk floor the driver keeps (default {DEFAULT_MIN_FREE_GB})")

    ing = sub.add_parser("ingest", help="ingest a probe index (or a canary) into the universe")
    ing.add_argument("universe")
    ing.add_argument("--library", default=None)
    ing.add_argument("--from", dest="source", required=True,
                     help="the probe's output directory (index.jsonl + manifest.json)")
    ing.add_argument("--checkout", default=None,
                     help="the library checkout at the pin, to read tags from source spans")
    ing.add_argument("--canary", action="store_true",
                     help="check the acceptance canary's index and record its toolchain")
    ing.add_argument("--accept-published", default=None, metavar="REASON",
                     help="ingest an index the library publishes itself (autoform-library-index/v0), "
                          "which the probe's canary never checked; the reason is recorded in "
                          "universe.yaml")

    bdb = sub.add_parser("build-db", help="build corpus.sqlite from the ingested indexes")
    bdb.add_argument("universe")
    bdb.add_argument("--force", action="store_true", help="rebuild even when it is current")
    bdb.add_argument("--blind", action="store_true",
                     help="build corpus.blind.sqlite instead: the G1-blind database, with no tags "
                          "and no Wikipedia links in the docstrings")

    st = sub.add_parser("status", help="libraries, canaries, indexes and the database")
    st.add_argument("universe")
    st.add_argument("--json", action="store_true", dest="as_json")

    se = sub.add_parser("search", help="word search over corpus.sqlite, as the host tool does")
    se.add_argument("universe")
    se.add_argument("words", nargs="+")
    se.add_argument("--library", default=None)
    se.add_argument("--limit", type=int, default=20)
    se.add_argument("--all-words", action="store_true",
                    help="only declarations matching every word (default: any word)")
    se.add_argument("--json", action="store_true", dest="as_json")

    sh = sub.add_parser("show", help="one declaration of corpus.sqlite")
    sh.add_argument("universe")
    sh.add_argument("library")
    sh.add_argument("declaration")
    sh.add_argument("--json", action="store_true", dest="as_json")


def run(args) -> int:
    command = getattr(args, "corpus_command", None)
    if not command:
        raise CfError("cf corpus needs a subcommand",
                      hint="init, add, probe-command, ingest, build-db, status, search or show")
    workspace = from_args(args)
    handler = {"init": _init, "add": _add, "probe-command": _probe_command, "ingest": _ingest,
               "build-db": _build_db, "status": _status, "search": _search,
               "show": _show}[command]
    return handler(workspace, args)


def format_table(rows: list[dict], columns: list[str]) -> str:
    return _format_table(columns, [[row.get(c) for c in columns] for row in rows])


def _load(workspace, universe_id: str) -> Universe:
    return Universe.load(workspace.paths.universe_yaml(universe_id))


# ---------------------------------------------------------------------------
# init, add


def _init(workspace, args) -> int:
    path = workspace.paths.universe_yaml(args.universe)
    universe = Universe.create(path, args.universe, args.frozen)
    path.parent.mkdir(parents=True, exist_ok=True)
    universe.save()
    print(f"created universe {universe.id} (frozen {universe.frozen}) at {path}")
    return 0


def _add(workspace, args) -> int:
    universe = _load(workspace, args.universe)
    if args.exclude is not None:
        row = universe.exclude(args.library, args.exclude)
        universe.save()
        print(f"excluded {row['id']} from {universe.id}: {row['reason']}")
        return 0
    missing = [flag for flag, value in (("--repo", args.repo), ("--commit", args.commit),
                                        ("--toolchain", args.toolchain), ("--roots", args.roots))
               if not value]
    if missing:
        raise CfError("a library needs " + ", ".join(missing),
                      hint="or record it as excluded with --exclude <reason>")
    conventions = src = dest = None
    if args.conventions:
        src = Path(args.conventions).expanduser()
        if not src.is_file():
            raise CfError(f"no conventions file at {src}")
        dest = workspace.paths.universe_conventions_md(args.universe, args.library)
        conventions = f"conventions/{dest.name}"
        if dest.exists() and dest.read_bytes() != src.read_bytes():
            raise CfError(f"{dest} already holds other conventions",
                          hint="edit that file in place (it may carry a confirmed_by stamp), or "
                               "remove it first; `cf corpus add` never overwrites one")
    # the row is validated BEFORE anything is written: a refused add changes nothing
    row = universe.add_library(library_id=args.library, repo=args.repo, commit=args.commit,
                               toolchain=args.toolchain, roots=list(args.roots),
                               brain_lib=args.brain_lib, licence=args.licence,
                               conventions=conventions)
    if conventions and row.get("conventions") != conventions:
        row["conventions"] = conventions
    if src is not None and not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    universe.save()
    print(f"{universe.id}: {row['id']} at {short(row['commit'])} ({row['toolchain']})")
    if not universe.canary_passed(row["toolchain"]):
        print(f"  note: no passed canary for Lean {lean_version(row['toolchain'])} yet; "
              f"`cf corpus probe-command {universe.id} --canary --toolchain {row['toolchain']}` "
              f"prints how to check one")
    return 0


# ---------------------------------------------------------------------------
# probe-command


def _probe_command(workspace, args) -> int:
    universe = _load(workspace, args.universe)
    if args.canary:
        if not args.toolchain:
            raise CfError("--canary needs --toolchain", hint="e.g. leanprover/lean4:v4.34.0")
        if not TOOLCHAIN.match(args.toolchain):
            raise CfError(f"--toolchain {args.toolchain!r} is not an elan toolchain",
                          hint="e.g. leanprover/lean4:v4.34.0 or "
                               "leanprover/lean4-nightly:nightly-2026-10-01")
        print(_canary_pipeline(universe, args.toolchain, args.atlas_root))
        return 0
    if not args.library:
        raise CfError("probe-command needs --library (or --canary --toolchain)")
    print(_library_pipeline(universe, universe.library(args.library), args.atlas_root,
                            args.min_free_gb))
    return 0


def _root_line(root: str | None) -> str:
    """Set ATLAS_ROOT once, so every later line names one directory even when it was unset.

    The driver defaults it to ``$HOME/atlas`` itself; a pasted ``"$ATLAS_ROOT"``
    that is empty would turn the clean-up into ``rm -rf /libs/...``.
    """
    if root:
        return f"export ATLAS_ROOT={shlex.quote(root)}"
    return 'export ATLAS_ROOT="${ATLAS_ROOT:-$HOME/atlas}"'


#: a path component the printed lines may put inside double quotes unescaped
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9._@-]+\Z")


def _slug(value: str) -> str:
    """A directory name for a toolchain: every character outside [A-Za-z0-9._-] as '-'."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "toolchain"


def _library_pipeline(universe: Universe, lib: dict, root: str | None, min_free_gb: int) -> str:
    commit = lib["commit"]
    lib_id = lib["id"]                       # [a-z0-9._-]: safe inside double quotes
    assert _SAFE_COMPONENT.match(lib_id)
    roots = ",".join(lib["roots"])
    tsv = f"{universe.id}.tsv"
    toolchain = lib["toolchain"]             # validated by universe.py: no shell characters
    lines = [
        f"# Library {lib_id} of universe {universe.id} at {commit} ({toolchain}).",
        "# Run on the allowlisted run host, as the operator: `cf` never builds a library.",
        "# Building a third-party library on a shared host is a trust decision the box owner",
        "# signs (docs/proposals/wikipedia-locate.md sections 6 and 13).",
        "#",
        "# 0. once per host: copy the drivers there, keeping their layout",
        "#      " + " ".join(DRIVERS),
        f"#    and check the canary of Lean {lean_version(toolchain)}: "
        f"`cf corpus probe-command {universe.id} --canary --toolchain {toolchain}`.",
        _root_line(root),
        "# 1. the universe-list line the driver reads for this library",
        f"printf '%s\\t%s\\t%s\\t%s\\t%s\\n' {shlex.quote(lib_id)} {shlex.quote(lib['repo'])} "
        f"{commit} {shlex.quote(roots)} build > {shlex.quote(tsv)}",
        "# 2. the disk floor (of the filesystem holding $ATLAS_ROOT, and of / and $HOME) is",
        "#    checked before every heavy step, the open-file limit raised to 65536, the olean",
        "#    caches fetched, the library built at the pin and probed, under nice/ionice in a",
        "#    clean environment:",
        f"MIN_FREE_GB={int(min_free_gb)} bash scripts/corpus/build_universe_host.sh "
        f"{shlex.quote(tsv)} {shlex.quote(lib_id)}",
        "# 3. bring the index back (the directory holds index.jsonl and manifest.json)",
        f'#      "$ATLAS_ROOT/index/{lib_id}@{commit[:12]}/"',
        "#    and ingest it here, with the checkout when tags should be read from source spans:",
        f"cf corpus ingest {universe.id} --library {lib_id} --from <the copied index dir> "
        f"--checkout <a clean checkout of {lib_id} at {commit[:12]}>",
        "# 4. on the host, remove the build and keep the index:",
        f'[ -n "$ATLAS_ROOT" ] && rm -rf -- "$ATLAS_ROOT/libs/{lib_id}/.lake"',
        "# 5. when every library is in: cf corpus build-db " + universe.id,
    ]
    return "\n".join(lines)


def _canary_pipeline(universe: Universe, toolchain: str, root: str | None) -> str:
    version = lean_version(toolchain)
    work = f'"$ATLAS_ROOT/canary/{_slug(version)}"'
    header = "module\\n\\npublic section"
    sed = shlex.quote(f"s#@@TOOLCHAIN@@#{toolchain}#;s#@@MODULE_HEADER@@#{header}#;"
                      f"s#@@MODULE_FOOTER@@#end#")
    clean = ('env -i HOME="$HOME" USER="$USER" PATH="$HOME/.elan/bin:/usr/bin:/bin" '
             'LANG=C.UTF-8 LC_ALL=C.UTF-8 nice -n 10 ionice -c3')
    lines = [
        f"# The acceptance canary of Lean {version} (docs/proposals/wikipedia-locate.md section 2.4),",
        "# on the run host. Copy templates/corpus/canary/ and the drivers there first.",
        _root_line(root),
        f"mkdir -p {work} && cp -R templates/corpus/canary/. {work}/",
        f"cd {work} && for f in $(find . -name '*.tmpl'); do",
        f'  sed -e {sed} "$f" > "${{f%.tmpl}}" && rm "$f"; done',
        "# (a toolchain without the module system: replace the header and footer with nothing)",
        "# the build runs as the probe does: a clean environment, nice and ionice",
        f"{clean} lake build",
        f"bash <drivers>/scripts/corpus/probe_host.sh {work} cf-canary CfCanary {work}/index",
        "# bring the index directory back, then here:",
        f"cf corpus ingest {universe.id} --canary --from <the copied index dir>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ingest, build-db


def _ingest(workspace, args) -> int:
    universe = _load(workspace, args.universe)
    source = Path(args.source).expanduser()
    if args.canary:
        result = ingest_mod.check_canary(universe, source)
        universe.save()
        verdict = "PASSED" if result["passed"] else "FAILED"
        print(f"canary of Lean {result['lean_version']}: {verdict}")
        for name, case in result["cases"].items():
            mark = "ok " if case["ok"] else "BAD"
            print(f"  {mark} {name}: expected {case['expected']}, got {case['got']}")
        return 0 if result["passed"] else 1
    if not args.library:
        raise CfError("ingest needs --library (or --canary)")
    checkout = Path(args.checkout).expanduser() if args.checkout else None
    if checkout is not None and not checkout.is_dir():
        raise CfError(f"no checkout directory at {checkout}")
    manifest = ingest_mod.ingest(universe, args.library, source,
                                 workspace.paths.universe_index_dir(args.universe, args.library),
                                 checkout=checkout, accept_published=args.accept_published)
    universe.save()
    statuses = ", ".join(f"{k} {v}" for k, v in manifest["by_proof_status"].items())
    print(f"{universe.id}/{args.library}: {manifest['declarations']} declarations "
          f"({statuses}), index {short(manifest['sha256'])}")
    for flag, count in manifest["flagged"].items():
        log.warn(f"{count} declaration(s) flagged {flag}: "
                 f"{ingest_mod.FLAG_MEANINGS.get(flag, 'see references/locate-documents.md section 8')}")
    dropped = manifest["private_collisions_dropped"]
    if dropped["count"]:
        log.warn(f"{dropped['count']} private declaration(s) dropped: their user name is "
                 f"another declaration's ({', '.join(dropped['names'][:5])}"
                 f"{', ...' if len(dropped['names']) > 5 else ''})")
    if manifest.get("published"):
        log.warn(f"{args.library}: a published index, accepted on trust "
                 f"({manifest['published']['reason']}); recorded in universe.yaml")
    if checkout is None:
        print("  tags: docstring URLs only; pass --checkout to read @[wikidata/stacks/kerodon] "
              "attributes from the source spans")
    print(f"  corpus.sqlite is now stale: `cf corpus build-db {universe.id}`")
    return 0


def _build_db(workspace, args) -> int:
    universe = _load(workspace, args.universe)
    db = (workspace.paths.corpus_db_blind(args.universe) if args.blind
          else workspace.paths.corpus_db(args.universe))
    result = build_db.build(universe, workspace.paths.universe_dir(args.universe), db,
                            force=args.force, blind=args.blind)
    if result["built"]:
        universe.save()
        what = "G1-blind: no tags, no docstring Wikipedia links" if args.blind else \
            f"{result['tags']} tags"
        print(f"built {db}: {result['declarations']} declarations of {result['libraries']} "
              f"libraries, {what}, sha256 {short(result['sha256'])}")
    else:
        print(f"{db} is current (sha256 {short(result['sha256'])}); --force rebuilds it")
    return 0


# ---------------------------------------------------------------------------
# status, search, show


def _status(workspace, args) -> int:
    universe = _load(workspace, args.universe)
    databases = {}
    for blind, path, recorded in (
            (False, workspace.paths.corpus_db(args.universe), universe.corpus_db),
            (True, workspace.paths.corpus_db_blind(args.universe), universe.corpus_db_blind)):
        state = "not built"
        if recorded:
            state = ("current" if build_db.current(universe, path, blind=blind)
                     else "STALE or changed")
        elif path.exists():
            state = "STALE (an index changed since it was built)"
        databases[path.name] = (recorded, state)
    recorded, db_state = databases["corpus.sqlite"]
    rows = []
    for lib in universe.libraries:
        index = lib.get("index") or {}
        conventions = lib.get("conventions")
        confirmed = False
        if conventions:
            conv = workspace.paths.universe_dir(args.universe) / conventions
            confirmed = conv.is_file() and "confirmed_by:" in conv.read_text(encoding="utf-8")
        rows.append({"library": lib["id"], "commit": short(lib["commit"]),
                     "lean": lean_version(lib["toolchain"]),
                     "canary": "passed" if universe.canary_passed(lib["toolchain"]) else "NO",
                     "declarations": index.get("declarations", "-"),
                     "index": short(index["sha256"]) if index.get("sha256") else "-",
                     "conventions": ("confirmed" if confirmed else
                                     "UNCONFIRMED" if conventions else "-")})
    if args.as_json:
        print(json.dumps({"universe": universe.id, "frozen": universe.frozen,
                          "universe_sha256": universe.sha256(), "libraries": rows,
                          "excluded": universe.excluded, "canaries": universe.canaries,
                          "corpus_db": recorded, "corpus_db_state": db_state,
                          "corpus_db_blind": databases["corpus.blind.sqlite"][0],
                          "corpus_db_blind_state": databases["corpus.blind.sqlite"][1]},
                         indent=2, sort_keys=True))
        return 0
    print(f"universe {universe.id} (frozen {universe.frozen}), sha256 {short(universe.sha256())}")
    if rows:
        print(format_table(rows, ["library", "commit", "lean", "canary", "declarations",
                                  "index", "conventions"]))
    else:
        print("  no libraries: `cf corpus add`")
    for row in universe.excluded:
        print(f"  excluded {row['id']}: {row['reason']}")
    for row in universe.canaries:
        print(f"  canary Lean {row.get('lean_version')}: "
              f"{'passed' if row.get('passed') else 'FAILED'} ({row.get('checked')})")
    for name, (row, state) in databases.items():
        if row:
            print(f"  {name}: {row.get('declarations')} declarations, sha256 "
                  f"{short(row.get('sha256') or '')}, {state}")
        elif name == "corpus.sqlite" or state != "not built":
            print(f"  {name}: {state}")
    return 0


def _open(workspace, universe_id: str):
    universe = _load(workspace, universe_id)
    db = workspace.paths.corpus_db(universe_id)
    conn = query.open_db(db)
    if query.meta(conn).get("universe_sha256") != universe.sha256():
        log.warn(f"{db} was built for another state of universe {universe_id}; "
                 f"`cf corpus build-db {universe_id}` rebuilds it")
    return conn


def _search(workspace, args) -> int:
    conn = _open(workspace, args.universe)
    try:
        results = query.search(conn, args.words, library=args.library, limit=args.limit,
                               all_words=args.all_words)
    finally:
        conn.close()
    if args.as_json:
        for row in results:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        return 0
    if not results:
        print("no declaration matched")
        return 0
    print(format_table([{"library": r["library"], "declaration": r["declaration"],
                         "kind": r["kind"], "score": r["score"]} for r in results],
                       ["library", "declaration", "kind", "score"]))
    return 0


def _show(workspace, args) -> int:
    conn = _open(workspace, args.universe)
    try:
        record = query.declaration(conn, args.library, args.declaration)
    finally:
        conn.close()
    if record is None:
        raise CfError(f"{args.library} has no declaration {args.declaration} in corpus.sqlite",
                      hint=f"`cf corpus search {args.universe} <words>` finds names")
    if args.as_json:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    for key in ("library", "name", "module", "path", "line", "end_line", "kind", "proof_status",
                "statement", "signature"):
        print(f"{key:13}: {record.get(key)}")
    if record.get("tags"):
        print("tags         : " + ", ".join(f"{t['tag']} {t['value']}" for t in record["tags"]))
    if record.get("docstring"):
        print("docstring    :")
        print("  " + "\n  ".join(str(record["docstring"]).splitlines()))
    return 0
