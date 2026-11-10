#!/usr/bin/env bash
# library_host.sh -- stage I1 of the informalize mode, on the run host: build one Lean library
# at its commit, probe it, check the probe's canary on its toolchain, dump its dependencies.
#
# New code for docs/proposals/informalize.md section 6.1. It is the one-library entry point
# over locate's host drivers, which it runs as they are: scripts/corpus/build_universe_host.sh
# (clone, HEAD check, open-file limit, caches, build, probe), templates/corpus/canary/ with
# scripts/corpus/probe_host.sh (the acceptance canary, as `cf corpus probe-command --canary`
# prints it), and scripts/detect/dump_deps.sh (DumpDeps). `cf informalize build-command`
# copies the drivers to the host with their layout and prints the line that starts this
# script detached; `cf informalize fetch-library` brings back what it collects. It never
# edits the library's sources. It writes under $ATLAS_ROOT, except what the tools keep in the
# operator's home on the host: Mathlib's download cache (~/.cache/mathlib), which the driver's
# `lake exe cache get` fills, and the toolchains elan installs (~/.elan).
#
# Usage, from the host folder (the workspace's remote_root):
#   ATLAS_ROOT=$PWD/library [MIN_FREE_GB=60] [LEAN_THREADS=16] \
#     bash drivers/scripts/informalize/library_host.sh library.tsv
#
# library.tsv is ONE universe line (scripts/corpus/universe_host.py): kind build, the ref a
# 40-hex commit, the roots the theorem's module, MODULE_PREFIXES one prefix, e.g.
#   <id>-lib <TAB> <url> <TAB> <commit> <TAB> <module> <TAB> build <TAB>
#   BUILD_TARGETS=<module>;MODULE_PREFIXES=<prefix>;LIBRARY_CACHE=0
#
# Steps:
#   0. host/drivers.json: the sha256 of every driver and of the library list this run reads
#      (library_host.py digest), which fetch-library compares with what it copied;
#   1. the universe driver on that line (its state is $ATLAS_ROOT/state/<list>/, <list> the
#      file's name without .tsv; its index $ATLAS_ROOT/index/<id>@<commit12>/);
#   2. the canary for the clone's lean-toolchain, in $ATLAS_ROOT/canary/<version>/, in the
#      module system's form when a module file the index names opens with `module`
#      (library_host.py);
#   3. dump_deps.sh in the built checkout, writing $ATLAS_ROOT/deps.json outside it, with the
#      open-file limit raised here (the driver's ended with the driver), in the drivers' clean
#      environment under nice and ionice, LEAN_NUM_THREADS from LEAN_THREADS;
#   4. collect into $ATLAS_ROOT/host/:
#        lean-toolchain lake-manifest.json  the checkout's
#        license/<name>      each license file at the checkout's root (LICENSE, COPYING, ...)
#        commit commit-time  HEAD, and the commit's committer time in seconds
#        state/ logs/        the driver's state and logs for the list
#        canary/             the canary's index.jsonl and manifest.json; canary-form
#        canary.log dump_deps.log
#        src.tar             `git archive` of the commit's .lean files (no .lake: it is not
#                            tracked; nothing else, so no file an agent would read as
#                            instructions)
#   5. host/DONE, holding the exit status, written last -- on a failure too.
#
# Environment: ATLAS_ROOT (required, absolute); MIN_FREE_GB, LEAN_THREADS and NICE_LEVEL,
# which the universe driver reads too (defaults 450, 24 and 10 there and here; the printed
# line passes 60 and 16); DUMP_TIMEOUT, seconds for the dependency dump (default 900).
# Exit status: 0 done; the driver's own status when it fails (1, or 3 at the disk floor);
# 2 bad arguments; 4 the canary; 5 the dump; 6 the collection (drivers.json included).
set -uo pipefail
umask 022

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s library_host: %s\n' "$(ts)" "$*"; }
die() { log "$*"; exit 2; }
fail() { local code=$1; shift; log "FAIL: $*"; exit "$code"; }

here=$(cd "$(dirname "$0")" && pwd)
drivers=$(cd "$here/../.." && pwd)
driver=$drivers/scripts/corpus/build_universe_host.sh
helper=$drivers/scripts/corpus/universe_host.py
probe_host=$drivers/scripts/corpus/probe_host.sh
dump=$drivers/scripts/detect/dump_deps.sh
tool=$here/library_host.py
canary_templates=$drivers/templates/corpus/canary

MIN_FREE_GB=${MIN_FREE_GB:-450}
LEAN_THREADS=${LEAN_THREADS:-24}
NICE_LEVEL=${NICE_LEVEL:-10}
DUMP_TIMEOUT=${DUMP_TIMEOUT:-900}
export MIN_FREE_GB LEAN_THREADS NICE_LEVEL
USER_NAME=${USER:-$(id -un)}
case $DUMP_TIMEOUT in ''|*[!0-9]*) die "DUMP_TIMEOUT must be a whole number of seconds" ;; esac

# The clean environment every tool runs in, and the heavy-step wrapper: the universe
# driver's own (scripts/corpus/build_universe_host.sh), so nothing a library's Lake
# configuration or scripts could read reaches them beyond HOME, USER and PATH.
cenv() {
  env -i HOME="$HOME" USER="$USER_NAME" PATH="$HOME/.elan/bin:/usr/bin:/bin" \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 GIT_TERMINAL_PROMPT=0 "$@"
}
heavy() {
  local assigns=()
  while [ "$#" -gt 0 ] && [[ $1 =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; do assigns+=("$1"); shift; done
  cenv LEAN_NUM_THREADS="$LEAN_THREADS" "${assigns[@]}" nice -n "$NICE_LEVEL" ionice -c3 "$@"
}
# git as the universe driver runs it (cflib.gitsafe's pins, every content filter of the
# checkout blanked, since `git archive` runs a smudge filter by the name an attribute in the
# tree chose); tests/test_informalize_library.py holds this copy to gitsafe's.
GIT_SAFE_CONFIG=(-c core.fsmonitor= -c core.hooksPath=/dev/null -c diff.external= -c core.pager=cat
  -c core.sshCommand= -c core.askPass=)
GIT_SAFE_ENV=(GIT_CONFIG_NOSYSTEM=1 GIT_OPTIONAL_LOCKS=0 GIT_TERMINAL_PROMPT=0)
git_env_pairs=unknown
g() {
  local dir="" key names=() pins=() pairs=() i=0
  [ "${1:-}" = -C ] && dir=${2:-}
  if [ -n "$dir" ] && [ -e "$dir/.git" ]; then
    while IFS= read -r key; do
      case $key in filter.*.clean|filter.*.smudge|filter.*.process) names+=("$key") ;; esac
    done < <(cenv "${GIT_SAFE_ENV[@]}" git "${GIT_SAFE_CONFIG[@]}" -C "$dir" config --list --name-only 2>/dev/null)
  fi
  for key in ${names[@]+"${names[@]}"}; do
    if [[ $key == *=* ]]; then
      if [ "$git_env_pairs" = unknown ]; then
        git_env_pairs=$(cenv "${GIT_SAFE_ENV[@]}" git "${GIT_SAFE_CONFIG[@]}" --version 2>/dev/null \
          | awk '{split($3, v, "."); print ((v[1] > 2 || (v[1] == 2 && v[2] >= 31)) ? "yes" : "no")}')
      fi
      if [ "$git_env_pairs" != yes ]; then
        printf 'library_host: refusing git in %s: it names the content filter %s, which this git cannot blank\n' "$dir" "$key" >&2
        return 126
      fi
    else
      pins+=(-c "$key=")
    fi
    pairs+=("GIT_CONFIG_KEY_$i=$key" "GIT_CONFIG_VALUE_$i=")
    i=$((i + 1))
  done
  [ "$i" -gt 0 ] && pairs+=("GIT_CONFIG_COUNT=$i")
  cenv "${GIT_SAFE_ENV[@]}" ${pairs[@]+"${pairs[@]}"} nice -n "$NICE_LEVEL" ionice -c3 \
    git "${GIT_SAFE_CONFIG[@]}" ${pins[@]+"${pins[@]}"} "$@"
}
py() { cenv python3 -I "$@"; }

[ "$#" -eq 1 ] || die "usage: ATLAS_ROOT=\$PWD/library bash library_host.sh <library.tsv>"
[ -n "${ATLAS_ROOT:-}" ] || die "ATLAS_ROOT is not set: the printed line sets it to \$PWD/library"
case $ATLAS_ROOT in /*) ;; *) die "ATLAS_ROOT must be an absolute path, not '$ATLAS_ROOT'" ;; esac
for f in "$driver" "$helper" "$probe_host" "$dump" "$tool" "$canary_templates/lakefile.toml.tmpl"; do
  [ -f "$f" ] || die "no $f: copy the drivers to the host with their layout (cf informalize build-command)"
done
universe=$(readlink -f "$1") || die "no library list $1"
[ -f "$universe" ] || die "no library list $universe"
uname=$(basename "$universe" .tsv)

# One run at a time; only the run holding the lock may touch host/.
mkdir -p "$ATLAS_ROOT/state" || die "cannot create $ATLAS_ROOT/state"
exec 8>"$ATLAS_ROOT/state/library_host.lock"
flock -n 8 || die "another library_host.sh holds $ATLAS_ROOT/state/library_host.lock"
host_dir=$ATLAS_ROOT/host
mkdir -p "$host_dir" || die "cannot create $host_dir"
rm -f "$host_dir/DONE"
# DONE is the last thing written, whatever ends the run, so a reader that sees it may read
# everything else in host/.
trap 'rc=$?; printf "%s\n" "$rc" > "$host_dir/DONE"; exit "$rc"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# 0. what this run reads, which fetch-library holds library/to-host/ to
py "$tool" digest --drivers "$drivers" --list "$universe" >"$host_dir/drivers.json" \
  || fail 6 "could not record the drivers' sha256 in $host_dir/drivers.json"

mapfile -t ids < <(py "$helper" ids --universe "$universe")
[ "${#ids[@]}" -eq 1 ] || die "$universe must list exactly one library; it lists ${#ids[@]}"
lib=${ids[0]}
entry=$(py "$helper" entry --universe "$universe" --id "$lib") || die "$universe did not parse"
eval "$entry"
[ "$LIB_KIND" = build ] || die "$lib is of kind $LIB_KIND; I1 builds its library (kind build)"
[ "$LIB_REF_IS_SHA" = 1 ] || die "$lib's ref $LIB_REF is not a 40-hex commit"
case $LIB_ROOTS in *,*|*.+|*.\*) die "$lib's roots '$LIB_ROOTS' are not one module" ;; esac
case $OPT_MODULE_PREFIXES in ''|*,*) die "$lib needs MODULE_PREFIXES=<one prefix>" ;; esac
commit=$LIB_REF module=$LIB_ROOTS prefix=$OPT_MODULE_PREFIXES
checkout=$ATLAS_ROOT/libs/$lib
index_dir=$ATLAS_ROOT/index/$lib@${commit:0:12}
state_dir=$ATLAS_ROOT/state/$uname
log "library $lib at $commit: build $module, probe the modules under $prefix; root $ATLAS_ROOT, floor ${MIN_FREE_GB}G, LEAN_THREADS=$LEAN_THREADS, LIBRARY_CACHE=$OPT_LIBRARY_CACHE"

# 1. the universe driver: clone, check HEAD, open-file limit, caches, build, probe
log "1. build_universe_host.sh"
bash "$driver" "$universe" "$lib"
rc=$?
[ "$rc" -eq 0 ] || fail "$rc" "build_universe_host.sh exited $rc (see $ATLAS_ROOT/logs/$uname/$lib.log)"
[ -f "$index_dir/manifest.json" ] || fail 1 "the driver finished without an index in $index_dir"
[ -d "$checkout/.lake" ] || fail 1 "$checkout holds no build (.lake); move the index aside and run again"

# 2. the acceptance canary on the library's toolchain
toolchain=$(tr -d '[:space:]' <"$checkout/lean-toolchain") || fail 4 "no lean-toolchain in $checkout"
slug=$(py "$tool" slug --toolchain "$toolchain") || fail 4 "lean-toolchain says '$toolchain', not a toolchain"
form=$(py "$tool" module-form --checkout "$checkout" --index "$index_dir/index.jsonl") \
  || fail 4 "could not read the module files the index of $lib names in $checkout"
canary=$ATLAS_ROOT/canary/$slug
log "2. the canary of $toolchain ($form form) in $canary"
rm -rf -- "$canary" && mkdir -p "$canary" && cp -R "$canary_templates/." "$canary/" \
  || fail 4 "could not copy the canary to $canary"
py "$tool" render-canary --dir "$canary" --toolchain "$toolchain" --form "$form" >/dev/null \
  || fail 4 "could not render the canary"
( cd "$canary" && heavy lake build ) >"$host_dir/canary.log" 2>&1 \
  || fail 4 "the canary did not build (see $host_dir/canary.log)"
heavy bash "$probe_host" "$canary" cf-canary CfCanary "$canary/index" >>"$host_dir/canary.log" 2>&1 \
  || fail 4 "the canary's probe failed (see $host_dir/canary.log)"

# 3. the dependency dump, outside the checkout
log "3. dump_deps.sh --root-module $module --module-prefix $prefix"
ulimit -n 65536 || fail 5 "cannot raise the open-file limit to 65536"
rm -f "$ATLAS_ROOT/deps.json"
heavy bash "$dump" --repo "$checkout" --out "$ATLAS_ROOT/deps.json" --root-module "$module" \
  --module-prefix "$prefix" --timeout "$DUMP_TIMEOUT" >"$host_dir/dump_deps.log" 2>&1 \
  || fail 5 "dump_deps.sh failed (see $host_dir/dump_deps.log)"

# 4. what fetch-library brings back
log "4. collect into $host_dir"
collect() {
  local name
  cp "$checkout/lean-toolchain" "$host_dir/lean-toolchain" || return 1
  cp "$checkout/lake-manifest.json" "$host_dir/lake-manifest.json" || return 1
  rm -rf -- "$host_dir/license" "$host_dir/state" "$host_dir/logs" "$host_dir/canary" || return 1
  mkdir -p "$host_dir/license" || return 1
  for name in LICENSE LICENSE.md LICENSE.txt LICENCE LICENCE.md LICENCE.txt COPYING COPYING.md \
              COPYING.txt LICENSE-APACHE LICENSE-MIT; do
    if [ -f "$checkout/$name" ] && [ ! -L "$checkout/$name" ]; then
      cp "$checkout/$name" "$host_dir/license/$name" || return 1
    fi
  done
  g -C "$checkout" rev-parse HEAD >"$host_dir/commit" || return 1
  g -C "$checkout" log -1 --format=%ct "$commit" >"$host_dir/commit-time" || return 1
  cp -R "$state_dir" "$host_dir/state" || return 1
  cp -R "$ATLAS_ROOT/logs/$uname" "$host_dir/logs" || return 1
  mkdir -p "$host_dir/canary" || return 1
  cp "$canary/index/index.jsonl" "$canary/index/manifest.json" "$host_dir/canary/" || return 1
  printf '%s\n' "$form" >"$host_dir/canary-form" || return 1
  rm -f "$host_dir/src.tar" "$host_dir/src.tar.partial"
  g -C "$checkout" archive --format=tar -o "$host_dir/src.tar.partial" "$commit" -- '*.lean' \
    || return 1
  mv -f "$host_dir/src.tar.partial" "$host_dir/src.tar"
}
collect || fail 6 "could not collect the outputs into $host_dir"
log "done: $index_dir, $ATLAS_ROOT/deps.json and $host_dir; bring them back with cf informalize fetch-library"
exit 0
