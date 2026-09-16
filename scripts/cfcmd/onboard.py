"""cf onboard -- Stage 0: adapter, conventions, dry run, state.json.

New code (design/stage_map.md Stage 0 "Onboard companion").  Onboarding is
complete when the adapter parses at least one tagged declaration and
conventions.md exists; the counts it prints are the ones every later stage is
reconciled against (the three hand-written tao-measure-lean parsers disagreed:
1,248 vs 1,414 vs 1,413 declarations, which is why the skill ships one).

    cf onboard ours --repo ~/Desktop/tao-measure-lean --adapter docstring_tags
    cf onboard adam --repo ~/Desktop/measure_theory --adapter attribution_table \\
        --owner external --write-policy read_only
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cflib import log, state as state_mod, vocab
from cflib.adapters_api import DRY_RUN_REQUIRED_KEYS
from cflib.campaign import from_args
from cflib.errors import AdapterError, CfError
from cflib.jsonio import atomic_write
from cflib.log import format_table
from cflib.templates import copy_template

#: Sensible starting points per adapter kind; edit adapter.yaml afterwards.
DEFAULTS = {
    "docstring_tags": {"glob": "**/*.lean", "anchor": "docstring", "granularity": "item"},
    "attribution_table": {"glob": "pdf-overlay/data/*.json", "anchor": "ledger", "granularity": "item"},
    "ledger_nodes": {"glob": "source-formalization-ledger.json", "anchor": "ledger", "granularity": "node"},
    "custom": {"glob": "", "anchor": "none", "granularity": "item"},
}


def register(subparsers) -> None:
    p = subparsers.add_parser("onboard", help="register a companion repo (adapter + conventions + state)")
    p.add_argument("name", help="short companion name used in every artifact (ours, adam, teorth)")
    p.add_argument("--repo", required=True, help="path to the companion repo (never written to)")
    p.add_argument("--adapter", required=True, choices=sorted(vocab.ADAPTER_KINDS),
                   help="statement-table adapter kind")
    p.add_argument("--owner", default="external",
                   help="who may change this repo (a session id, a person, or 'external')")
    p.add_argument("--write-policy", default="read_only", choices=list(vocab.WRITE_POLICIES),
                   help="v1 is findings-only; peers stay read_only")
    p.add_argument("--glob", help="files the adapter parses (repo-relative)")
    p.add_argument("--anchor", choices=sorted(vocab.ANCHOR_CONVENTIONS),
                   help="where page anchors come from")
    p.add_argument("--granularity", choices=sorted(vocab.GRANULARITIES),
                   help="item (default) or node (ledger repos: run `cf curate` first)")
    p.add_argument("--skip-dry-run", action="store_true",
                   help="write the files without running the parser")
    p.add_argument("--force", action="store_true", help="rewrite adapter.yaml if it exists")


def run(args) -> int:
    campaign = from_args(args)
    paths = campaign.paths
    name = args.name
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        raise CfError(f"--repo {repo} is not a directory")
    if repo == campaign.root or campaign.root in repo.parents:
        raise CfError("the companion repo must live outside the campaign directory")

    defaults = DEFAULTS[args.adapter]
    commit = _commit(repo)
    adapter_yaml = paths.adapter_yaml(name)
    paths.companion_dir(name).mkdir(parents=True, exist_ok=True)

    if adapter_yaml.exists() and not args.force:
        log.info(f"{adapter_yaml} exists; left untouched (use --force to rewrite)")
    else:
        text_values = {
            "name": name,
            "repo": str(repo),
            "commit": commit or "null",
            "adapter": args.adapter,
            "granularity": args.granularity or defaults["granularity"],
            "glob": _yaml_str(args.glob or defaults["glob"]),
            "anchor": args.anchor or defaults["anchor"],
            "write_policy": args.write_policy,
            "owner": _yaml_str(args.owner),
        }
        from cflib.templates import render_template
        atomic_write(adapter_yaml, render_template("adapter.yaml.example", text_values))
        log.ok(f"wrote {adapter_yaml}")

    conventions, written = copy_template("conventions.md.example", paths.conventions_md(name),
                                         {"name": name})
    if written:
        log.ok(f"wrote {conventions}")
    else:
        log.info(f"{conventions} exists; left untouched")

    counts, problem = _dry_run(adapter_yaml, repo, campaign, skip=args.skip_dry_run)

    with state_mod.update(campaign, name) as st:
        st["name"] = name
        st["repo"] = str(repo)
        st["commit"] = commit
        st["book_sha"] = campaign.book_sha
        adapter_state = {
            "kind": args.adapter,
            "yaml": str(adapter_yaml.relative_to(campaign.root)),
            "granularity": args.granularity or defaults["granularity"],
            "write_policy": args.write_policy,
            "owner": args.owner,
        }
        if counts is not None:
            adapter_state["dry_run"] = counts
        st["adapter"] = {**(st.get("adapter") or {}), **adapter_state}
        state_mod.note(st, "onboard", adapter=args.adapter, commit=(commit or "")[:7])
    log.ok(f"wrote {paths.state_json(name)}")

    _register_in_campaign_yaml(campaign, {
        "name": name, "repo": str(repo), "adapter": args.adapter,
        "owner": args.owner, "write_policy": args.write_policy,
    })

    _report(name, repo, commit, counts, problem, conventions)
    if counts is not None and not counts.get("tagged"):
        raise CfError(f"{name}: the adapter matched 0 tagged declarations",
                      hint="fix adapter.yaml (glob, tag pattern) and re-run `cf onboard --force`")
    return 0


# ---------------------------------------------------------------------------


def _commit(repo: Path) -> str | None:
    res = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else None


def _yaml_str(value: str) -> str:
    return '"' + str(value).replace('"', '\\"') + '"' if value else '""'


def _dry_run(adapter_yaml: Path, repo: Path, campaign, skip: bool):
    """Run the adapter's parser without writing a statement table."""
    if skip:
        log.warn("dry run skipped: onboarding is not complete until the parser runs")
        return None, "skipped"
    try:
        from cflib.adapters_api import load as load_adapter
        adapter = load_adapter(adapter_yaml, campaign=campaign)
        counts = dict(adapter.dry_run(repo))
    except AdapterError as exc:
        log.warn(str(exc))
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - a parser bug must not lose the files we wrote
        log.warn(f"dry run failed: {exc.__class__.__name__}: {exc}")
        return None, f"{exc.__class__.__name__}: {exc}"
    missing = [k for k in DRY_RUN_REQUIRED_KEYS if k not in counts]
    if missing:
        log.warn(f"adapter dry_run() omitted {', '.join(missing)}")
    return counts, None


def _report(name, repo, commit, counts, problem, conventions) -> None:
    log.info("")
    log.info(f"companion : {name}")
    log.info(f"repo      : {repo}" + (f" @ {commit[:7]}" if commit else " (not a git repo)"))
    if counts:
        rejects = counts.get("rejects") or []
        rows = [(k, v) for k, v in counts.items() if not isinstance(v, (list, dict))]
        log.info("")
        log.info(format_table(["count", "value"], rows))
        if rejects:
            log.info("")
            log.warn(f"{len(rejects)} item tags rejected by the strict grammar; first few:")
            for r in rejects[:5]:
                log.info(f"    {r.get('raw')!r}  ({r.get('reason')})")
    elif problem and problem != "skipped":
        log.warn(f"no counts recorded: {problem}")
    log.info("")
    log.info("next:")
    log.info(f"  edit {conventions} and have the repo owner confirm it")
    log.info(f"  cf extract --companion {name}")


def _register_in_campaign_yaml(campaign, entry: dict) -> None:
    """Add the companion to campaign.yaml's companions list, keeping comments.

    campaign.yaml is hand-edited and full of load-bearing comments, so this is a
    surgical text insert rather than a YAML re-dump. The directory on disk stays
    authoritative either way (Campaign.companion_names merges both).
    """
    if any(c.get("name") == entry["name"] for c in campaign.companions):
        log.detail(f"{entry['name']} already listed in campaign.yaml")
        return
    path = campaign.paths.campaign_yaml
    lines = path.read_text(encoding="utf-8").splitlines()
    block = [f"  - name: {entry['name']}",
             f"    repo: {entry['repo']}",
             f"    adapter: {entry['adapter']}",
             f"    owner: {entry['owner']}",
             f"    write_policy: {entry['write_policy']}"]
    idx = next((i for i, l in enumerate(lines) if re.match(r"^companions\s*:", l)), None)
    if idx is None:
        log.warn("campaign.yaml has no companions: key; add the entry by hand")
        return
    if re.match(r"^companions\s*:\s*(\[\s*\])?\s*$", lines[idx]):
        lines[idx] = "companions:"
        insert_at = idx + 1
    else:
        insert_at = idx + 1
        j = idx + 1
        while j < len(lines):
            line = lines[j]
            if not line.strip() or line.lstrip().startswith("#"):
                j += 1
                continue
            if line.startswith((" ", "\t", "-")):
                insert_at = j + 1
                j += 1
                continue
            break
    lines[insert_at:insert_at] = block
    atomic_write(path, "\n".join(lines) + "\n")
    log.ok(f"registered {entry['name']} in {path.name}")
