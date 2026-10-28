#!/usr/bin/env bash
# probe_host.sh -- index one BUILT Lean checkout with the library probe, on the run host.
#
# New code for docs/proposals/wikipedia-locate.md §2.3 (universes and library
# indexes): the operator runs this on the run host after building a library at
# its pin; it never builds anything itself. It renders
# templates/corpus/probe.lean.tmpl for the library, runs SHARD_COUNT probe
# shards (at most MAX_PARALLEL at once) under a clean environment, nice and
# ionice, then merges the shard files into a deterministic index.jsonl and a
# manifest.json through scripts/corpus/probe_merge.py.
#
# Usage:
#   probe_host.sh <checkout-dir> <library-id> <roots> <output-dir>
#
#   <roots>   the modules to import: comma/space separated, or @FILE (one per line)
#
# Environment (all optional):
#   SHARD_COUNT        shards to split the declarations into           (default 8)
#   MAX_PARALLEL       shards running at once; each is one Lean thread (default min(SHARD_COUNT, 24))
#   MODULE_PREFIXES    modules that count as the library's own         (default: the roots)
#   SRC_DIRS           source directories, relative to the checkout    (default ".")
#   PROBE_FIELD_CAP PROBE_MENTIONS_CAP PROBE_MAX_BYTES PROBE_INCLUDE_PRIVATE PROBE_PROGRESS
#                      passed to the probe; see the template's header comment
#   PROBE_TEMPLATE     the template                (default: ../../templates/corpus/ from here)
#   PROBE_NICE         nice level                                       (default 10)
#   KEEP_WORK=1        keep the per-shard files under <output-dir>/work after a clean merge
#   LIBRARY_KIND LIBRARY_DIR LIBRARY_COMMIT [LIBRARY_PATH_PREFIX LIBRARY_SRC_DIRS]
#                      a library probed inside <checkout-dir>, another library's built
#                      workspace: core (Lean's Init/Std, LIBRARY_DIR the toolchain's src/lean,
#                      LIBRARY_PATH_PREFIX src/) or package-in-workspace (LIBRARY_DIR its
#                      .lake/packages/<name>); the index then carries LIBRARY_COMMIT and paths
#                      inside the library's own repository (probe_merge.py LibrarySource)
#
# Output: <output-dir>/index.jsonl, <output-dir>/manifest.json, and
# <output-dir>/work/ (rendered probe, shard files, per-shard logs and timings).
# Exit status: 0 indexed; 1 a shard failed (its log is named); 2 bad arguments
# or a merge refusal.
#
# Needs bash 4.3 or later (`wait -n`). Each shard records its own exit status
# in work/shard-<i>.exit, since `wait -n -p` (which would name the shard that
# finished) is bash 5.1 and later; and a driver that stops early (a refusal, a
# failed merge, a signal from the universe driver's timeout) takes every shard
# still running with it.
set -euo pipefail

die() { printf 'probe_host: %s\n' "$*" >&2; exit 2; }

[ "$#" -eq 4 ] || die "usage: probe_host.sh <checkout-dir> <library-id> <roots> <output-dir>"
checkout=$(cd "$1" 2>/dev/null && pwd) || die "no checkout directory $1"
library=$2
roots=$3
out_dir=$4

here=$(cd "$(dirname "$0")" && pwd)
template=${PROBE_TEMPLATE:-$here/../../templates/corpus/probe.lean.tmpl}
merge_py=$here/probe_merge.py
[ -f "$template" ] || die "no probe template at $template"
[ -f "$merge_py" ] || die "no probe_merge.py beside this script"
[ -f "$checkout/lean-toolchain" ] || die "$checkout has no lean-toolchain"
[ -d "$checkout/.lake/build" ] || die "$checkout is not built (no .lake/build); build it first"

shard_count=${SHARD_COUNT:-8}
case $shard_count in ''|*[!0-9]*) die "SHARD_COUNT must be a positive integer" ;; esac
[ "$shard_count" -ge 1 ] || die "SHARD_COUNT must be at least 1"
default_parallel=$(( shard_count < 24 ? shard_count : 24 ))
parallel=${MAX_PARALLEL:-$default_parallel}
case $parallel in ''|*[!0-9]*) die "MAX_PARALLEL must be a positive integer" ;; esac
[ "$parallel" -ge 1 ] || die "MAX_PARALLEL must be at least 1"
nice_level=${PROBE_NICE:-10}

mkdir -p "$out_dir"
out_dir=$(cd "$out_dir" && pwd)
work=$out_dir/work
rm -rf "$work"
mkdir -p "$work"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# A clean environment: nothing a third-party Lake configuration could read
# beyond HOME, USER, PATH and the probe's own settings.
clean_env() {
  env -i HOME="$HOME" USER="${USER:-$(id -un)}" PATH="$HOME/.elan/bin:/usr/bin:/bin" \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 "$@"
}

clean_env python3 -I "$merge_py" render --template "$template" --library "$library" \
  --roots "$roots" --prefixes "${MODULE_PREFIXES:-}" --src-dirs "${SRC_DIRS:-.}" \
  --out "$work/probe.lean" || die "could not render the probe"

ulimit -n 65536 2>/dev/null || true
cd "$checkout"
# Resolve the Lake workspace once, serially, so the shards only read it.
clean_env nice -n "$nice_level" ionice -c3 lake env true >"$work/lake-env.log" 2>&1 \
  || die "lake env failed in $checkout (see $work/lake-env.log)"

run_one() {
  local i=$1
  clean_env LEAN_NUM_THREADS=1 \
    PROBE_OUTPUT="$work/shard-$i.jsonl" SHARD_INDEX="$i" SHARD_COUNT="$shard_count" \
    PROBE_FIELD_CAP="${PROBE_FIELD_CAP:-20000}" PROBE_MENTIONS_CAP="${PROBE_MENTIONS_CAP:-4096}" \
    PROBE_MAX_BYTES="${PROBE_MAX_BYTES:-2147483648}" \
    PROBE_INCLUDE_PRIVATE="${PROBE_INCLUDE_PRIVATE:-0}" PROBE_PROGRESS="${PROBE_PROGRESS:-0}" \
    nice -n "$nice_level" ionice -c3 \
    python3 -I "$merge_py" run-shard --timing "$work/shard-$i.timing.json" -- \
    lake env lean "$work/probe.lean" >"$work/shard-$i.log" 2>&1
}

# a shard is its subshell and everything under it (env, nice, python, lake, lean)
kill_tree() {
  local pid
  for pid in "$@"; do
    kill -STOP "$pid" 2>/dev/null || true
    kill_tree $(pgrep -P "$pid" 2>/dev/null || true)
    kill -TERM "$pid" 2>/dev/null || true
    kill -CONT "$pid" 2>/dev/null || true
  done
}
trap 'kill_tree $(jobs -pr)' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

run_recorded() {
  local i=$1 status=0
  run_one "$i" || status=$?
  printf '%s\n' "$status" >"$work/shard-$i.exit"
}
running=0
reap() {
  # Wait for any one shard to finish; its exit status is in its .exit file.
  wait -n || true
  running=$((running - 1))
}
for ((i = 0; i < shard_count; i++)); do
  if [ "$running" -ge "$parallel" ]; then reap; fi
  run_recorded "$i" &
  running=$((running + 1))
done
while [ "$running" -gt 0 ]; do reap; done
wait || true

failed=()
for ((i = 0; i < shard_count; i++)); do
  if [ "$(cat "$work/shard-$i.exit" 2>/dev/null || echo missing)" != 0 ]; then failed+=("$i"); fi
done

if [ "${#failed[@]}" -gt 0 ]; then
  for i in "${failed[@]}"; do
    printf 'probe_host: shard %s failed; last lines of %s:\n' "$i" "$work/shard-$i.log" >&2
    tail -n 20 "$work/shard-$i.log" >&2 || true
  done
  exit 1
fi

library_args=()
if [ -n "${LIBRARY_DIR:-}" ] || [ -n "${LIBRARY_KIND:-}" ]; then
  [ -n "${LIBRARY_KIND:-}" ] && [ -n "${LIBRARY_DIR:-}" ] && [ -n "${LIBRARY_COMMIT:-}" ] \
    || die "LIBRARY_KIND, LIBRARY_DIR and LIBRARY_COMMIT go together"
  library_args=(--library-kind "$LIBRARY_KIND" --library-dir "$LIBRARY_DIR"
    --library-commit "$LIBRARY_COMMIT" --library-path-prefix "${LIBRARY_PATH_PREFIX:-}"
    --library-src-dirs "${LIBRARY_SRC_DIRS:-.}")
fi
clean_env python3 -I "$merge_py" merge --work "$work" --shard-count "$shard_count" \
  --checkout "$checkout" --out-dir "$out_dir" --template "$template" \
  --probe "$work/probe.lean" --parallel "$parallel" --started "$started" \
  ${library_args[@]+"${library_args[@]}"} \
  || die "merge refused the shard files (they are kept in $work)"

if [ "${KEEP_WORK:-0}" != "1" ]; then
  rm -f "$work"/shard-*.jsonl
fi
