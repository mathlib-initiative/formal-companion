#!/usr/bin/env bash
# Render DumpDeps.lean and run it inside a companion's built Lean environment.
#
# Ported from adam-detector/README.md:6-13 ("run from the repo root with
# PATH=/usr/bin:$PATH lake env lean --run <path>/DumpDeps.lean ... Move deps.json
# OUT of the repo afterwards (the tree must stay clean)").
#
# Two rules this script enforces rather than trusts:
#   * the rendered .lean file and the deps.json output both live OUTSIDE the
#     companion tree, so the repo stays clean and nothing is ever committed;
#   * PATH is fronted with /usr/bin so a Homebrew python/node ahead of the
#     system ones cannot break `lake env`.
#
# This is the one step of the pipeline that runs Lean. `cf detect-proxies --lean`
# never calls it for you: it prints this command line and stops when deps.json
# is missing, because the dump needs a built .lake the skill must not rebuild.
#
# Usage:
#   dump_deps.sh --repo <companion root> --out <abs path to deps.json> \
#                --package <name> --root-module <Module> --module-prefix <Ns> \
#                [--lean <rendered .lean path>] [--template <tmpl>] [--timeout 900] [--dry-run]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
template="$here/DumpDeps.lean.tmpl"
repo="" out="" package="" root_module="" module_prefix="" lean_out="" timeout_s=900 dry_run=0

die() { printf 'error: %s\n' "$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="${2:?}"; shift 2 ;;
    --out) out="${2:?}"; shift 2 ;;
    --package) package="${2:?}"; shift 2 ;;
    --root-module) root_module="${2:?}"; shift 2 ;;
    --module-prefix) module_prefix="${2:?}"; shift 2 ;;
    --lean) lean_out="${2:?}"; shift 2 ;;
    --template) template="${2:?}"; shift 2 ;;
    --timeout) timeout_s="${2:?}"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) sed -n '1,25p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$repo" ] || die "--repo is required"
[ -n "$out" ] || die "--out is required (an absolute path outside the repo)"
[ -n "$root_module" ] || die "--root-module is required (e.g. MeasureTheory)"
[ -n "$module_prefix" ] || die "--module-prefix is required (e.g. MeasureTheory.Tao)"
[ -n "$package" ] || package="$root_module"
[ -f "$template" ] || die "template not found: $template"
[ -d "$repo" ] || die "companion root not found: $repo"
[ -d "$repo/.lake" ] || die "no .lake in $repo -- build the companion first (this script never builds)"

abspath() { python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$1"; }
repo_abs="$(abspath "$repo")"
out_abs="$(abspath "$out")"
case "$out_abs" in
  "$repo_abs"/*) die "--out is inside the companion tree ($repo_abs); write deps.json to the campaign's detect/ dir" ;;
esac
[ -n "$lean_out" ] || lean_out="$(dirname "$out_abs")/DumpDeps.lean"
lean_abs="$(abspath "$lean_out")"
case "$lean_abs" in
  "$repo_abs"/*) die "--lean is inside the companion tree ($repo_abs); render it next to deps.json" ;;
esac

mkdir -p "$(dirname "$out_abs")" "$(dirname "$lean_abs")"
sed -e "s|{{package}}|$package|g" \
    -e "s|{{root_module}}|$root_module|g" \
    -e "s|{{module_prefix}}|$module_prefix|g" \
    -e "s|{{output_path}}|$out_abs|g" \
    "$template" > "$lean_abs"
printf 'rendered %s\n' "$lean_abs"

# `timeout` is coreutils; macOS ships it only via Homebrew (gtimeout).
runner=()
if command -v timeout >/dev/null 2>&1; then runner=(timeout "$timeout_s")
elif command -v gtimeout >/dev/null 2>&1; then runner=(gtimeout "$timeout_s")
else printf 'warning: no timeout(1) found; running without a time limit\n' >&2
fi

# ${runner[@]+"${runner[@]}"} -- NOT "${runner[@]}": under bash 3.2 (what
# /usr/bin/env bash resolves to on a stock macOS) expanding an EMPTY array with
# `set -u` is an unbound-variable error, so the no-timeout path this script
# warns about above died on the very next line.
cmd=(env "PATH=/usr/bin:$PATH" ${runner[@]+"${runner[@]}"} lake env lean --run "$lean_abs")
printf 'cd %s && %s\n' "$repo_abs" "${cmd[*]}"
if [ "$dry_run" -eq 1 ]; then exit 0; fi

( cd "$repo_abs" && "${cmd[@]}" )

[ -s "$out_abs" ] || die "the dump did not write $out_abs"
printf 'wrote %s (%s bytes)\n' "$out_abs" "$(wc -c < "$out_abs" | tr -d ' ')"

# The repo must be exactly as we found it.
if git -C "$repo_abs" rev-parse --git-dir >/dev/null 2>&1; then
  if [ -n "$(git -C "$repo_abs" status --porcelain)" ]; then
    printf 'warning: %s has uncommitted changes after the dump -- check nothing was written into the tree\n' \
      "$repo_abs" >&2
  fi
fi
