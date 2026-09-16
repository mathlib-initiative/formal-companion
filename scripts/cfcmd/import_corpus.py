"""cf import-corpus -- adopt an existing findings directory as a run of this campaign.

New code (the design's "import the baseline corpus as runs").  The 2,672-file
corpus at ``~/Desktop/tao-diff-run`` was produced before this skill existed: its
findings are schema v1, they carry no ``run_id``, no ``finding_id`` and no
``book_sha``, and nothing records which edition they were judged against.  This
command lifts such a directory into ``corpus/<run-id>/`` as schema v2 and writes
the same RECEIPT.json ``cf harvest`` writes, so an imported run is
indistinguishable downstream from one this skill ran itself::

    cf import-corpus --run-id findings-hunt --from ~/Desktop/tao-diff-run/findings-hunt \
        --mode attack --preset attack-hunt

The SOURCE IS NEVER MODIFIED -- not renamed, not stamped, not deleted.  The
conversion happens on the way in (``gate/convert_v1.convert_doc``), which is why
``--book-sha`` matters: v1 rows were judged against whatever edition the run had,
and an imported row that silently inherits the campaign's edition is exactly the
draft-vs-published confusion the edition gate exists to catch.  Pass
``--book-sha`` when the findings were judged against a different edition than the
campaign pins; the receipt's edition audit then reports the mismatch instead of
hiding it.

Ported from: recovered/b174aa13_L5690_Bash.txt (harvest-from-journal) for the
receipt discipline, and scripts/gate/convert_v1.py for the v1 -> v2 mapping.
"""
from __future__ import annotations

import json
from pathlib import Path

from cflib import log
from cflib.campaign import from_args
from cflib.errors import CfError
from cflib.jsonio import write_json

from gate import convert_v1
from harvest import harvest as harvest_mod

from cfcmd.harvest import _record_state, _tally

#: Directory names the baseline used, mapped to (mode, preset).  Only a default:
#: --mode and --preset always win.
BASELINE_PRESETS = {
    "findings": ("differential", "differential-text"),
    "findings-pdf": ("differential", "differential-pdf"),
    "findings-gpt": ("differential", "differential-gpt"),
    "findings-vac": ("attack", "attack-vac"),
    "findings-hunt": ("attack", "attack-hunt"),
}


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "import-corpus",
        help="adopt an existing findings directory as corpus/<run-id> (v1 -> v2, with a receipt)")
    p.add_argument("--run-id", required=True, help="the run id these findings become")
    p.add_argument("--from", dest="source", required=True,
                   help="a LOCAL directory of findings JSON (read-only)")
    p.add_argument("--mode", choices=["differential", "attack"],
                   help="default: guessed from the source directory name")
    p.add_argument("--preset", default=None, help="recorded in every row's `preset`")
    p.add_argument("--book-sha", default=None,
                   help="the edition these findings were judged against "
                        "(default: the campaign's; pass the real one for an older run)")
    p.add_argument("--repos", default=None,
                   help="comma-separated companion ids (default: the campaign's)")
    p.add_argument("--no-convert", action="store_true",
                   help="copy verbatim; only for a directory that is already schema v2")
    p.add_argument("--no-tally", action="store_true", help="skip recomputing the tally")
    p.add_argument("--force", action="store_true",
                   help="re-import over an existing corpus/<run-id>")


def run(args) -> int:
    campaign = from_args(args)
    src = Path(args.source).expanduser()
    if not src.is_dir():
        raise CfError(f"no such directory: {src}",
                      hint="--from takes a LOCAL directory; a remote run is `cf harvest`")
    dest = Path(campaign.paths.corpus_run_dir(args.run_id)).resolve()
    if src.resolve() == dest:
        raise CfError(f"refusing to import {src} onto itself",
                      hint="the source is read-only by design; pick a different --run-id")

    mode, preset = _mode_and_preset(args, src)
    book_sha = args.book_sha or campaign.book_sha or ""
    repos = ([r.strip() for r in args.repos.split(",") if r.strip()]
             if args.repos else list(campaign.companion_names()))

    expected = sorted(p.name for p in src.glob("*.json")
                      if p.name != harvest_mod.RECEIPT_NAME)
    if not expected:
        raise CfError(f"{src} holds no findings JSON",
                      hint="expected <item>.json files, as the gate writes them")

    previous = harvest_mod.read_receipt(dest)
    if previous and not args.force:
        log.warn(f"corpus/{args.run_id} already exists ({previous.get('actual', {}).get('count')} "
                 f"files); re-importing over it (pass --force to silence this)")
    dest.mkdir(parents=True, exist_ok=True)

    written, converted, already_v2 = _import_files(
        src, dest, expected, args.no_convert,
        run_id=args.run_id, book_sha=book_sha, mode=mode, preset=preset, repos=repos)

    receipt = harvest_mod.build_receipt(
        run_id=args.run_id,
        source={"kind": "import", "path": str(src), "mode": mode, "preset": preset,
                "converted": converted, "already_v2": already_v2},
        expected=expected, dest=dest, book_sha=book_sha, previous=previous)
    receipt["expected_from"] = f"the source listing of {src} ({len(expected)} files)"
    write_json(dest / harvest_mod.RECEIPT_NAME, receipt, indent=1, sort_keys=True)

    log.info(f"imported {written} finding(s) from {src} -> {dest}")
    log.detail(f"  mode {mode}  preset {preset}  book_sha {book_sha[:12] or '(none)'}  "
               f"converted {converted}, already v2 {already_v2}")
    log.info(harvest_mod.describe(receipt))
    if not receipt["complete"]:
        log.warn("import INCOMPLETE: every number derived from this corpus is a lower bound.")

    counts = {} if args.no_tally else _tally(campaign, args.run_id, dest)
    _record_state(campaign, args.run_id, {"mode": mode, "preset": preset}, receipt, counts)
    return 0 if receipt["complete"] else 1


def _mode_and_preset(args, src: Path) -> tuple[str, str]:
    """--mode/--preset win; otherwise guess from the baseline directory name."""
    guess_mode, guess_preset = BASELINE_PRESETS.get(src.name, ("differential", ""))
    return (args.mode or guess_mode, args.preset if args.preset is not None else guess_preset)


def _import_files(src: Path, dest: Path, expected, no_convert: bool, *, run_id: str,
                  book_sha: str, mode: str, preset: str, repos) -> tuple[int, int, int]:
    """Write each source finding into *dest*, converting v1 rows on the way in."""
    written = converted = already_v2 = 0
    for name in expected:
        doc = json.loads((src / name).read_text(encoding="utf-8"))
        if no_convert or doc.get("schema_version") == 2:
            already_v2 += 1
            out = doc
        else:
            out = convert_v1.convert_doc(
                doc, run_id=run_id, book_sha=book_sha, mode=mode, preset=preset, repos=repos)
            converted += 1
        (dest / name).write_text(
            json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        written += 1
    return written, converted, already_v2
