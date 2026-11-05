#!/usr/bin/env bash
# verso_host.sh -- the overlay's hovers, on the run host: build one Lean library's literate
# HTML with Verso at its commit, and cut the committed pdf-overlay/verso.json out of it.
#
# New code for docs/proposals/informalize.md section 10. The informal companion's cards show
# each declaration's plain source, because the output repository's site has no Verso pages of
# its own; docs/overlay.md section 5 lets such a companion commit a `pdf-overlay/verso.json`
# cut from a Verso build of the commit its cards pin, made elsewhere. This script is that
# build and that cut, on the host that already built the library for stage I1:
# `cf informalize verso-command` copies the drivers, the library list and the output
# repository's annotation bundle to the host and prints the line that starts this script
# detached; the operator brings `verso/host/verso.json` back and commits it.
#
# It never writes into the I1 tree ($ATLAS_ROOT): the work checkout is its own copy under
# $VERSO_ROOT, because the Verso require edits the library's lakefile. It never edits the
# library's sources.
#
# Usage, from the host folder (the workspace's remote_root):
#   ATLAS_ROOT=$PWD/library VERSO_ROOT=$PWD/verso VERSO_REV=v4.36.0-rc2 [LEAN_THREADS=16] \
#     bash verso/drivers/scripts/informalize/verso_host.sh verso/library.tsv \
#       verso/annotations.bundle.json
#
# library.tsv is the ONE universe line stage I1 read (scripts/corpus/universe_host.py).
#
# Steps:
#   1. the work checkout $VERSO_ROOT/libs/<id>, at the line's commit: a copy of I1's
#      $ATLAS_ROOT/libs/<id> when that is there (its built dependencies with it), else a
#      clone of the line's URL;
#   2. the Verso require at $VERSO_REV, written before the library's first require, and
#      `lake update verso` -- refused when it moves any package the library was already
#      pinned to (verso_host.py moved), since the cards pin this tree;
#   3. `lake exe cache get` when the library builds against Mathlib, then `lake build
#      <the module the line names>`, which is what stage I1 built (never the whole
#      library: the literate build pulls in what else the cards' modules need);
#   4. Verso's literate.toml, naming the modules the bundle's cards are in and no others
#      (verso_host.py targets; a library that ships one of its own keeps it), then
#      `lake build $VERSO_TARGET` (`:literateHtml`), whose pages hold Verso's hover table;
#   5. templates/overlay/runtime/tools/verso_extract.py --pages over them, against the
#      bundle, writing $VERSO_ROOT/host/verso.json;
#   6. collect into $VERSO_ROOT/host/: verso.json, verso.counts.json, the work checkout's
#      lean-toolchain and lake-manifest.json, and the logs;
#   7. host/DONE, holding the exit status, written last -- on a failure too.
#
# Environment: ATLAS_ROOT and VERSO_ROOT (required, absolute) and VERSO_REV (required);
# LEAN_THREADS and NICE_LEVEL (defaults 24 and 10, as the universe driver reads them; the
# printed line passes 16); VERSO_TARGET (default :literateHtml).
# Exit status: 0 done; 2 bad arguments; 3 the work checkout; 4 the require or the pins;
# 5 the build; 6 the extraction or the collection.
set -uo pipefail
umask 022

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s verso_host: %s\n' "$(ts)" "$*"; }
die() { log "$*"; exit 2; }
fail() { local code=$1; shift; log "FAIL: $*"; exit "$code"; }

here=$(cd "$(dirname "$0")" && pwd)
drivers=$(cd "$here/../.." && pwd)
helper=$drivers/scripts/corpus/universe_host.py
tool=$here/verso_host.py
extract=$drivers/templates/overlay/runtime/tools/verso_extract.py

LEAN_THREADS=${LEAN_THREADS:-24}
NICE_LEVEL=${NICE_LEVEL:-10}
VERSO_TARGET=${VERSO_TARGET:-:literateHtml}
export LEAN_THREADS NICE_LEVEL
USER_NAME=${USER:-$(id -un)}

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
# git as the universe driver runs it (cflib.gitsafe's pins); this script runs no filter-bearing
# command (no archive, no checkout of a tree it did not clone itself).
GIT_SAFE_CONFIG=(-c core.fsmonitor= -c core.hooksPath=/dev/null -c diff.external= -c core.pager=cat
  -c core.sshCommand= -c core.askPass=)
g() {
  cenv GIT_CONFIG_NOSYSTEM=1 GIT_OPTIONAL_LOCKS=0 GIT_TERMINAL_PROMPT=0 \
    nice -n "$NICE_LEVEL" ionice -c3 git "${GIT_SAFE_CONFIG[@]}" "$@"
}
py() { cenv python3 -I "$@"; }

[ "$#" -eq 2 ] || die "usage: ATLAS_ROOT=\$PWD/library VERSO_ROOT=\$PWD/verso VERSO_REV=<rev> bash verso_host.sh <library.tsv> <annotations.bundle.json>"
[ -n "${ATLAS_ROOT:-}" ] || die "ATLAS_ROOT is not set: the printed line sets it to \$PWD/library"
[ -n "${VERSO_ROOT:-}" ] || die "VERSO_ROOT is not set: the printed line sets it to \$PWD/verso"
[ -n "${VERSO_REV:-}" ] || die "VERSO_REV is not set: the Verso revision of the library's toolchain"
case $ATLAS_ROOT in /*) ;; *) die "ATLAS_ROOT must be an absolute path, not '$ATLAS_ROOT'" ;; esac
case $VERSO_ROOT in /*) ;; *) die "VERSO_ROOT must be an absolute path, not '$VERSO_ROOT'" ;; esac
[ "$ATLAS_ROOT" != "$VERSO_ROOT" ] || die "VERSO_ROOT must not be ATLAS_ROOT: this script never writes into the I1 tree"
for f in "$helper" "$tool" "$extract"; do
  [ -f "$f" ] || die "no $f: copy the drivers to the host with their layout (cf informalize verso-command)"
done
universe=$(readlink -f "$1") || die "no library list $1"
[ -f "$universe" ] || die "no library list $universe"
bundle=$(readlink -f "$2") || die "no annotation bundle $2"
[ -f "$bundle" ] || die "no annotation bundle $bundle"

# One run at a time; only the run holding the lock may touch host/.
mkdir -p "$VERSO_ROOT/state" || die "cannot create $VERSO_ROOT/state"
exec 8>"$VERSO_ROOT/state/verso_host.lock"
flock -n 8 || die "another verso_host.sh holds $VERSO_ROOT/state/verso_host.lock"
host_dir=$VERSO_ROOT/host
mkdir -p "$host_dir" || die "cannot create $host_dir"
rm -f "$host_dir/DONE"
# DONE is the last thing written, whatever ends the run, so a reader that sees it may read
# everything else in host/.
trap 'rc=$?; printf "%s\n" "$rc" > "$host_dir/DONE"; exit "$rc"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mapfile -t ids < <(py "$helper" ids --universe "$universe")
[ "${#ids[@]}" -eq 1 ] || die "$universe must list exactly one library; it lists ${#ids[@]}"
lib=${ids[0]}
entry=$(py "$helper" entry --universe "$universe" --id "$lib") || die "$universe did not parse"
eval "$entry"
[ "$LIB_REF_IS_SHA" = 1 ] || die "$lib's ref $LIB_REF is not a 40-hex commit"
case $LIB_ROOTS in *,*|*.+|*.\*) die "$lib's roots '$LIB_ROOTS' are not one module" ;; esac
commit=$LIB_REF url=$LIB_URL module=$LIB_ROOTS
work=$VERSO_ROOT/libs/$lib
built=$ATLAS_ROOT/libs/$lib
log "library $lib at $commit: Verso $VERSO_REV, target $VERSO_TARGET, work $work"

# 1. the work checkout: I1's build copied, or a clone
if [ ! -d "$work/.git" ]; then
  mkdir -p "$VERSO_ROOT/libs" || fail 3 "cannot create $VERSO_ROOT/libs"
  rm -rf -- "$work.partial"
  if [ -d "$built/.git" ]; then
    log "1. copy I1's checkout $built (its built dependencies with it)"
    cp -a "$built" "$work.partial" || fail 3 "could not copy $built to $work.partial"
  else
    log "1. clone $url (I1 left no checkout at $built)"
    g clone --no-checkout "$url" "$work.partial" || fail 3 "could not clone $url"
  fi
  mv -f "$work.partial" "$work" || fail 3 "could not move $work.partial into place"
fi
g -C "$work" checkout --detach --force "$commit" || fail 3 "$work has no commit $commit"
head=$(g -C "$work" rev-parse HEAD) || fail 3 "cannot read $work's HEAD"
[ "$head" = "$commit" ] || fail 3 "$work is at $head, not $commit"
log "1. $work at $head, toolchain $(tr -d '[:space:]' <"$work/lean-toolchain")"

# 2. the Verso require, and the pins it may not move
py "$tool" require --lakefile "$work/lakefile.toml" --rev "$VERSO_REV" || fail 4 "could not require verso"
py "$tool" pins --manifest "$work/lake-manifest.json" >"$host_dir/pins-before.json" \
  || fail 4 "could not read $work/lake-manifest.json"
log "2. lake update verso"
( cd "$work" && heavy lake update verso ) >"$host_dir/lake_update.log" 2>&1 \
  || fail 4 "lake update verso failed (see $host_dir/lake_update.log)"
if ! py "$tool" moved --before "$host_dir/pins-before.json" \
     --manifest "$work/lake-manifest.json" >"$host_dir/moved.txt"; then
  log "$(cat "$host_dir/moved.txt")"
  fail 4 "adding verso $VERSO_REV moved the library's own pins (above): the build would not be of the tree the cards pin; pass a VERSO_REV whose dependencies the library already has"
fi

# 3. the library itself, as I1 built it
if grep -q '"mathlib"' "$host_dir/pins-before.json"; then
  log "3. lake exe cache get"
  ( cd "$work" && heavy lake exe cache get ) >"$host_dir/cache_get.log" 2>&1 \
    || fail 5 "lake exe cache get failed (see $host_dir/cache_get.log)"
fi
ulimit -n 65536 || fail 5 "cannot raise the open-file limit to 65536"
log "3. lake build $module"
( cd "$work" && heavy lake build "$module" ) >"$host_dir/lake_build.log" 2>&1 \
  || fail 5 "lake build $module failed (see $host_dir/lake_build.log)"

# 4. the literate pages: the modules the cards name, and no others
if g -C "$work" ls-files --error-unmatch literate.toml >/dev/null 2>&1; then
  log "4. the library has a literate.toml of its own; it chooses the modules"
else
  targets=$(py "$tool" targets --bundle "$bundle" --out "$work/literate.toml") \
    || fail 6 "could not write $work/literate.toml"
  log "4. $targets"
fi
log "4. lake build $VERSO_TARGET"
if ! ( cd "$work" && heavy lake build "$VERSO_TARGET" ) >"$host_dir/literate_build.log" 2>&1; then
  if grep -q "missing data file for module" "$host_dir/literate_build.log"; then
    log "the pages' tool could not read the modules this build imports (missing data file): this Verso reads the library's build at another level than the toolchain writes it, so VERSO_REV=$VERSO_REV does not match $(tr -d '[:space:]' <"$work/lean-toolchain")"
  fi
  fail 5 "lake build $VERSO_TARGET failed (see $host_dir/literate_build.log)"
fi
pages=$work/.lake/build/literate-html
if [ ! -f "$pages/-verso-docs.json" ]; then
  pages=$(dirname "$(find "$work/.lake/build" -maxdepth 3 -name '-verso-docs.json' -print -quit)")
fi
[ -f "$pages/-verso-docs.json" ] \
  || fail 5 "the build wrote no Verso pages with a hover table (-verso-docs.json) under $work/.lake/build"
log "4. pages $pages ($(du -sh "$pages" | cut -f1))"

# 5. the cut: each card's statement and its hovers
log "5. verso_extract.py --pages"
py "$extract" --pages "$pages" --bundle "$bundle" --out "$host_dir/verso.json" \
  --commit "$commit" >"$host_dir/verso.counts.json" 2>"$host_dir/verso_extract.log" \
  || { log "$(cat "$host_dir/verso_extract.log")"; fail 6 "verso_extract.py failed"; }
log "5. $(cat "$host_dir/verso.counts.json")"

# 6. what the operator brings back
log "6. collect into $host_dir"
{
  cp "$work/lean-toolchain" "$host_dir/lean-toolchain" &&
  cp "$work/lake-manifest.json" "$host_dir/lake-manifest.json" &&
  printf '%s\n' "$VERSO_REV" >"$host_dir/verso-rev"
} || fail 6 "could not collect the outputs into $host_dir"
log "done: $host_dir/verso.json; bring it back and commit it as pdf-overlay/verso.json"
exit 0
