#!/usr/bin/env bash
# build_universe_host.sh -- fetch, build and index every library of a universe list, on the run host.
#
# New code for docs/proposals/wikipedia-locate.md §2.3 (universes) and §2.4 (library
# indexes). §2.4 says `cf` never builds a library: the operator runs, per library, a disk
# floor check, `ulimit -n 65536`, the cache fetch, the build and the probe. This script is
# that pipeline over a whole universe list, run on the host, resumable, and careful with a
# disk the host shares with other users. It never edits a library's sources.
#
# Usage:
#   build_universe_host.sh <universe.tsv> [<library-id> ...]    build/index (only the ids given)
#   build_universe_host.sh --status <universe.tsv>              print the status table
#
# The universe list format is documented in universe_host.py (beside this script):
#   id <TAB> repo-url <TAB> ref <TAB> roots <TAB> kind [<TAB> KEY=VALUE;...]
#
# Per library, in file order:
#   1. resolve the ref (commit, branch or tag) to a commit, and skip the library when
#      <ATLAS_ROOT>/index/<id>@<commit12>/ already holds a finished index for this entry
#      (manifest.json present and consistent, universe-entry.json matching);
#   2. clone (partial, --filter=blob:none; cone sparse checkout when SPARSE is set) into
#      <ATLAS_ROOT>/libs/<id>/ and check out the commit detached;
#   kind=build:
#   3. give the workspace its Mathlib-family packages (mathlib batteries aesop Qq
#      proofwidgets plausible LeanSearchClient importGraph Cli, whichever the manifest pins)
#      as `cp -al` hard-link copies of a registered donor whose packages sit at exactly the
#      manifest's revs and URLs (re-verified with git first), so 40 libraries on one Mathlib
#      pin share one 7 GB copy. Git metadata outside .git/objects is copied, not linked.
#      After Lake resolves the workspace the links are checked (Lake re-clones a package whose
#      URL it dislikes), `lake build --no-build Mathlib` must pass before anything is built
#      (no accidental Mathlib compile), and after the build the donor's family packages must
#      hold no file modified since the link (a shared inode written in place). A shared write
#      disables linking for the rest of the run (<ATLAS_ROOT>/state/LINKING_DISABLED);
#   4. when nothing was linked: `lake exe cache get` for a Mathlib-dependent package (or
#      Mathlib itself), and the library's own published olean cache when it ships
#      scripts/lake-cache-get.sh (TauCeti), with RUNNER_TEMP and LAKE_CACHE_DIR under
#      <ATLAS_ROOT>;
#   5. `lake build <targets>` (skipped when `lake build --no-build <targets>` already passes);
#      a workspace that fetched its own family packages becomes a donor;
#   6. probe_host.sh with the expanded roots, SHARD_COUNT shards, then universe-entry.json;
#   kind=published-index:
#   3. copy the index the library publishes (INDEX_FILE in the tagged tree, else the release
#      asset of that name) into <ATLAS_ROOT>/index/<id>@<commit12>/ and write manifest.json.
#      When the tag commit only adds that file, <commit12> is its parent: the commit the
#      index describes.
#   kind=core / package-in-workspace (no clone: the library is inside WORKSPACE, a built
#   checkout libs/<WORKSPACE>):
#   2. core: the ref must resolve to the Lean.githash of the workspace's toolchain, whose
#      shipped src/lean holds the sources; package-in-workspace: the ref must resolve to the
#      rev the workspace's lake-manifest pins PACKAGE at, from this line's URL, and
#      .lake/packages/<PACKAGE> must be checked out there;
#   3. expand the roots in the library's own sources, check the workspace built every root
#      (its .olean exists), and probe inside the workspace; the index records the library's
#      own commit and its paths inside the library's own repository (core: src/Init/...).
#
# Every git, lake and probe command runs in a clean environment (env -i HOME USER PATH LANG)
# under nice and ionice -c3, with LEAN_NUM_THREADS capped. One library is processed at a
# time, so at most one heavy job runs. Before each big step the free space is checked on
# every filesystem the step can write -- the one holding $ATLAS_ROOT (clones, builds,
# caches, indexes), / and $HOME (elan toolchains) -- and the smallest counts; below
# MIN_FREE_GB (+ the step's estimate), or when df cannot say, the driver stops (exit 3).
#
# Environment (all optional):
#   ATLAS_ROOT           everything lives here                  (default $HOME/atlas)
#   MIN_FREE_GB          never let $ATLAS_ROOT's (or /, $HOME's) filesystem fall below this
#                        many GiB; a whole number, refused otherwise (default 450)
#   LEAN_THREADS         LEAN_NUM_THREADS for lake               (default 24)
#   NICE_LEVEL           nice level                              (default 10)
#   LINK_FAMILY=0        never hard-link Mathlib-family packages
#   ALLOW_MATHLIB_BUILD=1  build even when Mathlib is not fully restored from its cache
#   ALLOW_RECHECKOUT=1   move an already-built checkout to another commit (breaks donors)
#   SKIP_FAILED=1        do not retry libraries whose last attempt failed
#
# Layout: libs/<id>/ (checkouts), index/<id>@<commit12>/ (outputs), logs/<universe>/<id>.log
# (per library), state/<universe>/<id>.json and status.tsv (progress), state/donors.tsv
# (hard-link donors), tmp/ (scratch). Exit status: 0 all done, 1 some library failed,
# 2 bad arguments, 3 stopped at the disk floor.
set -uo pipefail
umask 022

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$*"; }
die() { printf '%s build_universe: %s\n' "$(ts)" "$*" >&2; exit 2; }

here=$(cd "$(dirname "$0")" && pwd)
helper=$here/universe_host.py
probe_host=$here/probe_host.sh
[ -f "$helper" ] && [ -f "$probe_host" ] || die "universe_host.py and probe_host.sh must sit beside this script"

ATLAS_ROOT=${ATLAS_ROOT:-$HOME/atlas}
MIN_FREE_GB=${MIN_FREE_GB:-450}
# The floor must be a whole number of GiB: `[ x -lt 450G ]` is an error, the `if` takes
# its else branch, and a floor that never stops anything is worse than none.
case $MIN_FREE_GB in ''|*[!0-9]*) die "MIN_FREE_GB must be a whole number of GiB, not '$MIN_FREE_GB'" ;; esac
LEAN_THREADS=${LEAN_THREADS:-24}
NICE_LEVEL=${NICE_LEVEL:-10}
LINK_FAMILY=${LINK_FAMILY:-1}
ALLOW_MATHLIB_BUILD=${ALLOW_MATHLIB_BUILD:-0}
ALLOW_RECHECKOUT=${ALLOW_RECHECKOUT:-0}
SKIP_FAILED=${SKIP_FAILED:-0}
USER_NAME=${USER:-$(id -un)}

# The clean environment every tool runs in: nothing a third-party Lake configuration (or the
# library's own scripts) could read beyond HOME, USER, PATH and what the step itself sets.
cenv() {
  env -i HOME="$HOME" USER="$USER_NAME" PATH="$HOME/.elan/bin:/usr/bin:/bin" \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 GIT_TERMINAL_PROMPT=0 "$@"
}
# heavy [NAME=value ...] command ... : clean env, thread cap, nice, idle I/O class.
heavy() {
  local assigns=()
  while [ "$#" -gt 0 ] && [[ $1 =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; do assigns+=("$1"); shift; done
  cenv LEAN_NUM_THREADS="$LEAN_THREADS" "${assigns[@]}" nice -n "$NICE_LEVEL" ionice -c3 "$@"
}
# git, as cflib.gitsafe.run makes it (universe_host.py and probe_merge.py inline the same
# pins; tests/test_locate_corpus_field.py holds all three equal to gitsafe's). A library
# checkout is a third party's tree: the fixed pins stop git from running an fsmonitor hook,
# a hooks path, an external diff, a pager, an ssh command or an askpass program it names;
# the environment ignores /etc/gitconfig and takes no optional lock. Every content filter
# the checkout resolves is blanked too, since `checkout` runs a smudge filter and `status`
# a clean one by the name an attribute in the tree chose: as `-c <key>=` for a name without
# an `=`, and as GIT_CONFIG_COUNT pairs (git 2.31 or later) for every name. A name with an
# `=` on an older git has no blank at all, and there git is not run.
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
        printf 'build_universe: refusing git in %s: it names the content filter %s, which this git cannot blank\n' "$dir" "$key" >&2
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
py() { cenv python3 -I "$helper" "$@"; }

if [ "${1:-}" = "--status" ]; then
  [ "$#" -eq 2 ] || die "usage: build_universe_host.sh --status <universe.tsv>"
  u=$(readlink -f "$2") || die "no universe file $2"
  py summary --universe "$u" --state-dir "$ATLAS_ROOT/state/$(basename "$u" .tsv)"
  exit $?
fi

[ "$#" -ge 1 ] || die "usage: build_universe_host.sh <universe.tsv> [<library-id> ...]"
universe=$(readlink -f "$1") || die "no universe file $1"
[ -f "$universe" ] || die "no universe file $universe"
shift
only_ids=("$@")
uname=$(basename "$universe" .tsv)

libs_dir=$ATLAS_ROOT/libs
index_root=$ATLAS_ROOT/index
log_dir=$ATLAS_ROOT/logs/$uname
state_dir=$ATLAS_ROOT/state/$uname
tmp_root=$ATLAS_ROOT/tmp
donors=$ATLAS_ROOT/state/donors.tsv
link_off=$ATLAS_ROOT/state/LINKING_DISABLED
mkdir -p "$libs_dir" "$index_root" "$log_dir" "$state_dir" "$tmp_root"

exec 9>"$ATLAS_ROOT/state/driver.lock"
flock -n 9 || die "another build_universe_host.sh holds $ATLAS_ROOT/state/driver.lock"
echo "$$" >"$state_dir/driver.pid"
ulimit -n 65536 || die "cannot raise the open-file limit to 65536 (Mathlib's cache unpacker needs it)"

mapfile -t all_ids < <(py ids --universe "$universe")
[ "${#all_ids[@]}" -gt 0 ] || die "the universe list did not parse (see above)"
for want in "${only_ids[@]}"; do
  printf '%s\n' "${all_ids[@]}" | grep -qxF -- "$want" || die "$want is not in $universe"
done

# The free GiB of the fullest filesystem a step can write: $ATLAS_ROOT's, /'s and $HOME's.
# Empty when df cannot answer, which ensure_space treats as a stop, never as room.
avail_gb() {
  df -BG --output=avail "$ATLAS_ROOT" / "$HOME" 2>/dev/null | tail -n +2 | tr -dc '0-9\n' \
    | grep -E '^[0-9]+$' | sort -n | head -n 1
}

# ---------------------------------------------------------------------------
# one library; runs in a subshell, so `fail` and `stop` just exit it
# ---------------------------------------------------------------------------
process_library() {
  local id=$1
  eval "$(py entry --universe "$universe" --id "$id")" || { log "FAIL $id: bad universe entry"; return 1; }
  local lg=$log_dir/$id.log st=$state_dir/$id.json
  local dir=$libs_dir/$id started_epoch df_start
  started_epoch=$(date +%s)
  df_start=$(avail_gb)
  set_state() { py state --file "$st" "$@" >/dev/null 2>>"$lg" || true; }
  fail() {
    log "FAIL $id: $*"
    set_state stage=failed "error=$*" "failed_at=$(ts)" "wall_seconds=$(( $(date +%s) - started_epoch ))"
    exit 1
  }
  stop() {
    log "STOP $id: $*"
    set_state stage=stopped "error=$*" "failed_at=$(ts)"
    exit 3
  }
  ensure_space() {   # <GB the step may add> <what>
    local need=$1 what=$2 avail
    avail=$(avail_gb)
    case $avail in ''|*[!0-9]*) stop "df could not report the free space before $what" ;; esac
    log "  df: ${avail}G free before $what (estimate +${need}G, floor ${MIN_FREE_GB}G)"
    if [ $(( avail - need )) -lt "$MIN_FREE_GB" ]; then
      stop "$what may need ~${need}G but the fullest of \$ATLAS_ROOT, / and \$HOME has ${avail}G free; the floor is ${MIN_FREE_GB}G"
    fi
  }
  step() { log "  $id: $*"; printf '\n=== %s %s\n' "$(ts)" "$*" >>"$lg"; }

  if [ "$SKIP_FAILED" = 1 ] && [ -f "$st" ] && grep -q '"stage": "failed"' "$st"; then
    log "skip $id: its last attempt failed and SKIP_FAILED=1"
    return 0
  fi
  log "== $id ($LIB_KIND, ref $LIB_REF)"
  set_state stage=resolving "ref=$LIB_REF" "kind=$LIB_KIND" "started_at=$(ts)" error= note=

  # 1. resolve the ref to a commit, without a clone when the ref is already one
  local commit ref_kind
  if [ "$LIB_REF_IS_SHA" = 1 ]; then
    commit=$LIB_REF ref_kind=commit
  else
    local listing
    listing=$(g ls-remote "$LIB_URL" "refs/heads/$LIB_REF" "refs/tags/$LIB_REF" "refs/tags/$LIB_REF^{}" 2>>"$lg") \
      || fail "git ls-remote $LIB_URL failed"
    local peeled tag head
    peeled=$(awk -v r="refs/tags/$LIB_REF^{}" '$2 == r {print $1}' <<<"$listing")
    tag=$(awk -v r="refs/tags/$LIB_REF" '$2 == r {print $1}' <<<"$listing")
    head=$(awk -v r="refs/heads/$LIB_REF" '$2 == r {print $1}' <<<"$listing")
    if [ -n "$head" ] && [ -n "$tag" ]; then fail "$LIB_REF is both a branch and a tag"; fi
    if [ -n "$peeled" ]; then commit=$peeled ref_kind=tag
    elif [ -n "$tag" ]; then commit=$tag ref_kind=tag
    elif [ -n "$head" ]; then commit=$head ref_kind=branch
    else fail "$LIB_URL has no branch or tag $LIB_REF"; fi
  fi
  set_state "commit=$commit" "ref_kind=$ref_kind"

  local out=""
  if [ "$LIB_KIND" != published-index ]; then
    out=$index_root/$id@${commit:0:12}
    local why rc
    why=$(py index-ok --universe "$universe" --id "$id" --commit "$commit" --out-dir "$out" 2>>"$lg"); rc=$?
    case $rc in
      0) log "skip $id: $out is $why"
         set_state stage=indexed "note=skipped: $why" $(py manifest-brief --manifest "$out/manifest.json")
         return 0 ;;
      2) local aside="$out.superseded-$(date -u +%Y%m%dT%H%M%SZ)"
         log "  $id: $out is $why; moving it to $(basename "$aside")"
         mv "$out" "$aside" || fail "could not move $out aside" ;;
    esac
  fi

  if [ "$LIB_KIND" = core ] || [ "$LIB_KIND" = package-in-workspace ]; then
    probe_in_workspace
  else
    checkout_library
    if [ "$LIB_KIND" = published-index ]; then
      publish_index
    else
      build_and_probe
    fi
  fi
  local wall=$(( $(date +%s) - started_epoch ))
  set_state stage=indexed "finished_at=$(ts)" "wall_seconds=$wall" "index_dir=$out" \
    "disk_delta_gb=$(( df_start - $(avail_gb) ))" $(py manifest-brief --manifest "$out/manifest.json")
  log "done $id in ${wall}s -> $out"
  return 0
}

# 2. the checkout: clone (partial; sparse when SPARSE is set) and check out the commit.
checkout_library() {
  local fresh=0
  if [ -e "$dir" ] && [ ! -d "$dir/.git" ]; then fail "$dir exists but is not a git checkout"; fi
  if [ ! -d "$dir/.git" ]; then
    ensure_space 3 "the clone of $id"
    step "clone $LIB_URL"
    rm -rf "$dir.partial"
    g clone --quiet --filter=blob:none --no-checkout "$LIB_URL" "$dir.partial" >>"$lg" 2>&1 \
      || fail "git clone failed"
    if [ -n "$OPT_SPARSE" ]; then
      # shellcheck disable=SC2086
      g -C "$dir.partial" sparse-checkout set --cone ${OPT_SPARSE//,/ } >>"$lg" 2>&1 \
        || fail "git sparse-checkout failed"
    fi
    mv "$dir.partial" "$dir" || fail "could not move the clone into place"
    fresh=1
  else
    local origin
    origin=$(g -C "$dir" remote get-url origin 2>>"$lg")
    [ "${origin%.git}" = "${LIB_URL%.git}" ] || fail "$dir fetches from $origin, the universe says $LIB_URL"
  fi
  if ! g -C "$dir" cat-file -e "$commit^{commit}" 2>/dev/null; then
    step "fetch ${commit:0:12}"
    g -C "$dir" fetch --quiet --filter=blob:none origin "$commit" >>"$lg" 2>&1 || fail "git fetch of $commit failed"
  fi
  local head_now
  head_now=$(g -C "$dir" rev-parse -q --verify HEAD 2>/dev/null)
  if [ "$fresh" = 1 ] || [ "$head_now" != "$commit" ]; then
    if [ "$fresh" = 0 ] && [ -d "$dir/.lake" ] && [ "$ALLOW_RECHECKOUT" != 1 ]; then
      fail "$dir is built at ${head_now:0:12}; the universe wants ${commit:0:12} (move it away, or ALLOW_RECHECKOUT=1)"
    fi
    step "check out ${commit:0:12}"
    g -C "$dir" checkout --quiet --detach "$commit" >>"$lg" 2>&1 || fail "git checkout failed"
  fi
  [ "$(g -C "$dir" rev-parse HEAD)" = "$commit" ] || fail "HEAD is not $commit after checkout"
  set_state stage=checked-out "checkout=$dir"
}

# kind=core / package-in-workspace: a library that lives inside another library's built
# workspace -- Lean core (Init, Std) of the workspace's toolchain, or a Lake package in its
# .lake/packages -- probed there, and recorded at its OWN commit with paths inside its OWN
# repository (docs/proposals/wikipedia-locate.md section 2.3). Nothing is cloned or built.
probe_in_workspace() {
  local ws=$libs_dir/$OPT_WORKSPACE roots_file=$state_dir/$id.roots prefixes_file=$state_dir/$id.prefixes
  local library_dir olean_dir path_prefix="" toolchain
  [ -d "$ws/.lake/build" ] && [ -f "$ws/lean-toolchain" ] \
    || fail "$ws is not a built workspace: build $OPT_WORKSPACE (a kind build library) first"
  toolchain=$(tr -d '[:space:]' <"$ws/lean-toolchain") || fail "no lean-toolchain in $ws"
  set_state "workspace=$OPT_WORKSPACE" "toolchain=$toolchain"
  if [ "$LIB_KIND" = core ]; then
    local lean githash tc
    lean=$(cd "$ws" && cenv elan which lean 2>>"$lg") || fail "elan which lean failed in $ws"
    githash=$(cenv "$lean" --githash 2>>"$lg") || fail "$lean --githash failed"
    [ "$githash" = "$commit" ] \
      || fail "$LIB_REF is ${commit:0:12}, but the Lean of $OPT_WORKSPACE ($toolchain) is ${githash:0:12}"
    tc=$(cd "$(dirname "$lean")/.." && pwd -P) || fail "no toolchain directory above $lean"
    library_dir=$tc/src/lean olean_dir=$tc/lib/lean path_prefix=src/
  else
    local pin rev subdir head pkg=$ws/.lake/packages/$OPT_PACKAGE
    pin=$(py package-pin --checkout "$ws" --package "$OPT_PACKAGE" --url "$LIB_URL" 2>>"$lg") \
      || fail "$OPT_WORKSPACE's lake-manifest.json does not pin $OPT_PACKAGE from $LIB_URL (see $lg)"
    IFS=$'\t' read -r rev subdir <<<"$pin"
    [ "$rev" = "$commit" ] \
      || fail "$LIB_REF is ${commit:0:12}, but $OPT_WORKSPACE pins $OPT_PACKAGE at ${rev:0:12}"
    head=$(g -C "$pkg" rev-parse HEAD 2>>"$lg")
    [ "$head" = "$rev" ] || fail "$pkg is at ${head:0:12}, the manifest pins ${rev:0:12}"
    library_dir=$pkg${subdir:+/$subdir} path_prefix=${subdir:+$subdir/}
    olean_dir=$library_dir/.lake/build/lib/lean
  fi
  [ -d "$library_dir" ] || fail "no source directory $library_dir"
  set_state stage=checked-out "checkout=$library_dir"
  step "expand roots $LIB_ROOTS in $library_dir"
  py expand-roots --checkout "$library_dir" --roots "$LIB_ROOTS" --src-dir "$OPT_SRC_DIR" \
    --out "$roots_file" --prefixes-out "$prefixes_file" >>"$lg" 2>&1 || fail "the roots did not expand (see $lg)"
  if [ -n "$OPT_MODULE_PREFIXES" ]; then
    tr ',' '\n' <<<"$OPT_MODULE_PREFIXES" | sort -u >"$prefixes_file"
  fi
  py check-oleans --olean-dir "$olean_dir" --roots-file "$roots_file" >>"$lg" 2>&1 \
    || fail "$OPT_WORKSPACE has not built every root module of $id (see $lg)"
  set_state "roots_modules=$(wc -l <"$roots_file")" "prefixes=$(paste -sd, "$prefixes_file")"
  ensure_space 3 "the probe of $id"
  set_state stage=probing
  step "probe_host.sh in $ws (kind $LIB_KIND, SHARD_COUNT=$OPT_SHARD_COUNT, $(wc -l <"$roots_file") root modules)"
  cenv SHARD_COUNT="$OPT_SHARD_COUNT" ${OPT_MAX_PARALLEL:+MAX_PARALLEL="$OPT_MAX_PARALLEL"} \
    MODULE_PREFIXES="@$prefixes_file" PROBE_NICE="$NICE_LEVEL" \
    LIBRARY_KIND="$LIB_KIND" LIBRARY_DIR="$library_dir" LIBRARY_COMMIT="$commit" \
    LIBRARY_PATH_PREFIX="$path_prefix" LIBRARY_SRC_DIRS="$OPT_SRC_DIR" \
    timeout --kill-after=120 "$OPT_PROBE_TIMEOUT" bash "$probe_host" "$ws" "$id" "@$roots_file" "$out" \
    >>"$lg" 2>&1 || fail "probe_host.sh failed (see $lg and $out/work/)"
  py write-entry --universe "$universe" --id "$id" --commit "$commit" --out-dir "$out" \
    "workspace=$OPT_WORKSPACE" "workspace_commit=$(g -C "$ws" rev-parse HEAD 2>/dev/null)" \
    "checkout=$library_dir" "toolchain=$toolchain" 2>>"$lg" \
    || fail "could not write $out/universe-entry.json"
}

# kind=published-index: copy what the library publishes, then count and check it.
publish_index() {
  local idx=$OPT_INDEX_FILE tag_commit=$commit source blob="" source_commit parent changed started
  started=$(ts)
  if g -C "$dir" cat-file -e "$tag_commit:$idx" 2>/dev/null; then
    source="tree:$idx"
    blob=$(g -C "$dir" rev-parse "$tag_commit:$idx")
  elif [ "$ref_kind" = tag ]; then
    source="asset:${LIB_URL%.git}/releases/download/$LIB_REF/$idx"
  else
    fail "$idx is not in the tree at ${commit:0:12}, and $LIB_REF is not a tag with release assets"
  fi
  # The index describes the commit it was generated from: the tag commit's parent when the
  # tag commit adds nothing but the index file.
  source_commit=$tag_commit
  parent=$(g -C "$dir" rev-parse -q --verify "$tag_commit^1" 2>/dev/null)
  if [ -n "$parent" ]; then
    changed=$(g -C "$dir" diff --name-only "$parent" "$tag_commit" 2>>"$lg")
    [ "$changed" = "$idx" ] && source_commit=$parent
  fi
  out=$index_root/$id@${source_commit:0:12}
  local why rc
  why=$(py index-ok --universe "$universe" --id "$id" --commit "$source_commit" --out-dir "$out" 2>>"$lg"); rc=$?
  if [ "$rc" = 0 ]; then
    log "skip $id: $out is $why"
    return 0
  fi
  if [ "$rc" = 2 ]; then
    mv "$out" "$out.superseded-$(date -u +%Y%m%dT%H%M%SZ)" || fail "could not move $out aside"
  fi
  ensure_space 2 "the published index of $id"
  local stage=$out.partial
  rm -rf "$stage"; mkdir -p "$stage"
  step "copy ${source} (described commit ${source_commit:0:12}, tag commit ${tag_commit:0:12})"
  if [ -n "$blob" ]; then
    g -C "$dir" show "$tag_commit:$idx" >"$stage/$idx" 2>>"$lg" || fail "git show $idx failed"
    [ "$(g hash-object --no-filters "$stage/$idx")" = "$blob" ] || fail "$idx does not hash to its tree blob $blob"
  else
    cenv curl -fsSL --retry 3 -o "$stage/$idx" "${source#asset:}" 2>>"$lg" || fail "download of ${source#asset:} failed"
  fi
  py published-manifest --index "$stage/$idx" --out "$stage/manifest.json" --universe "$universe" \
    --id "$id" --commit "$source_commit" --tag-commit "$tag_commit" --source "$source" \
    --blob-sha "$blob" --checkout "$dir" --started "$started" >>"$lg" 2>&1 \
    || fail "the published index failed its checks (see $lg)"
  mv "$stage" "$out" || fail "could not move $stage into place"
  py write-entry --universe "$universe" --id "$id" --commit "$source_commit" --out-dir "$out" \
    "tag_commit=$tag_commit" "checkout=$dir" 2>>"$lg" || fail "could not write $out/universe-entry.json"
  set_state "commit=$source_commit" "tag_commit=$tag_commit"
}

# Hard-link the family packages of $1 (a donor checkout) into this workspace.
link_family() {
  local donor=$1; shift
  local name src dst
  mkdir -p "$dir/.lake/packages"
  for name in "$@"; do
    src=$donor/.lake/packages/$name dst=$dir/.lake/packages/$name
    rm -rf "$dst.cflink"
    heavy cp -al "$src" "$dst.cflink" 2>>"$lg" || return 1
    # Git rewrites its own metadata in place (config, HEAD, refs, index): copy it.
    while IFS= read -r -d '' f; do
      cp -p "$f" "$f.cfcopy" && mv -f "$f.cfcopy" "$f" || return 1
    done < <(find "$dst.cflink/.git" -path "$dst.cflink/.git/objects" -prune -o -type f -print0)
    mv "$dst.cflink" "$dst" || return 1
  done
}

# The inode of a file every package has, to tell a linked package from a re-cloned one.
probe_inode() {
  local f
  for f in lakefile.lean lakefile.toml; do
    [ -f "$1/$f" ] && { stat -c %i "$1/$f"; return; }
  done
  echo none
}
# How many names that file has: above 1, the package shares its inodes with another workspace.
link_count() {
  local f
  for f in lakefile.lean lakefile.toml; do
    [ -f "$1/$f" ] && { stat -c %h "$1/$f"; return; }
  done
  echo 0
}

build_and_probe() {
  local roots_file=$state_dir/$id.roots prefixes_file=$state_dir/$id.prefixes why rc
  step "expand roots $LIB_ROOTS"
  py expand-roots --checkout "$dir" --roots "$LIB_ROOTS" --src-dir "$OPT_SRC_DIR" \
    --out "$roots_file" --prefixes-out "$prefixes_file" >>"$lg" 2>&1 || fail "the roots did not expand (see $lg)"
  if [ -n "$OPT_MODULE_PREFIXES" ]; then
    tr ',' '\n' <<<"$OPT_MODULE_PREFIXES" | sort -u >"$prefixes_file"
  fi
  set_state "roots_modules=$(wc -l <"$roots_file")" "prefixes=$(paste -sd, "$prefixes_file")"
  why=$(py index-ok --universe "$universe" --id "$id" --commit "$commit" --out-dir "$out" \
    --roots-file "$roots_file" --prefixes-file "$prefixes_file" 2>>"$lg"); rc=$?
  case $rc in
    0) log "  $id: $out is $why"
       py write-entry --universe "$universe" --id "$id" --commit "$commit" --out-dir "$out" "adopted=1" 2>>"$lg"
       return 0 ;;
    2) local aside="$out.superseded-$(date -u +%Y%m%dT%H%M%SZ)"
       log "  $id: $out is $why; moving it to $(basename "$aside")"
       mv "$out" "$aside" || fail "could not move $out aside" ;;
  esac

  local targets=()
  IFS=',' read -r -a targets <<<"${OPT_BUILD_TARGETS:-$LIB_BASES}"
  local toolchain
  toolchain=$(tr -d '[:space:]' <"$dir/lean-toolchain") || fail "no lean-toolchain"
  step "toolchain $toolchain"
  cenv elan toolchain install "$toolchain" >>"$lg" 2>&1 || fail "elan toolchain install $toolchain failed"
  set_state "toolchain=$toolchain"

  cd "$dir" || fail "cannot enter $dir"
  local relation
  relation=$(py family --checkout "$dir" 2>>"$lg") || fail "cannot read lake-manifest.json"

  # 3. hard-link the Mathlib family from a donor, when one matches exactly. A workspace whose
  # mathlib package already shares inodes (a donor others link to, or an earlier linked
  # attempt) is "shared": nothing may unpack into it again, so it never runs the cache fetch.
  local donor="" link_names=() shared=0 marker=$tmp_root/$id.link-marker
  if [ "$relation" = mathlib ] && [ "$(link_count "$dir/.lake/packages/mathlib")" -gt 1 ]; then
    shared=1
    log "  $id: its mathlib package already shares inodes; no cache fetch will touch it"
  fi
  if [ "$relation" = mathlib ] && [ "$shared" = 0 ] && [ "$LINK_FAMILY" = 1 ] && [ "$OPT_LINK_FAMILY" = 1 ] && [ ! -e "$link_off" ]; then
    local found
    if found=$(py find-donor --checkout "$dir" --donors "$donors" 2>>"$lg"); then
      donor=$(head -n 1 <<<"$found")
      mapfile -t link_names < <(tail -n +2 <<<"$found")
      ensure_space 2 "hard-linking ${#link_names[@]} packages into $id"
      step "hard-link ${link_names[*]} from $donor"
      if link_family "$donor" "${link_names[@]}"; then
        set_state "linked_from=$donor"
        shared=1
        # From here on, nothing may modify a file the donor sees; checked after the build.
        touch "$marker"
        sleep 1
      else
        log "  $id: linking failed; removing the partial links and fetching instead"
        local name
        for name in "${link_names[@]}"; do rm -rf "$dir/.lake/packages/$name" "$dir/.lake/packages/$name.cflink"; done
        donor="" link_names=()
      fi
    else
      log "  $id: no hard-link donor ($(tail -n 1 "$lg" | cut -c1-160))"
    fi
  fi

  step "resolve the workspace (lake env true)"
  heavy lake env true >>"$lg" 2>&1 || fail "lake could not resolve the workspace (see $lg)"
  if [ -n "$donor" ]; then
    local name
    for name in "${link_names[@]}"; do
      [ "$(probe_inode "$dir/.lake/packages/$name")" = "$(probe_inode "$donor/.lake/packages/$name")" ] \
        || fail "lake replaced the linked package $name (re-cloned it); not building"
    done
  fi

  local artifact_env=() built=0
  if heavy lake build --no-build "${targets[@]}" >>"$lg" 2>&1; then
    log "  $id: ${targets[*]} already up to date"
    built=1
  else
    # 4. caches, when nothing was linked
    if [ "$shared" = 0 ] && { [ "$relation" = mathlib ] || [ "$relation" = self ]; }; then
      ensure_space 15 "the Mathlib cache of $id"
      step "lake exe cache get"
      heavy lake exe cache get >>"$lg" 2>&1 || heavy lake exe cache get >>"$lg" 2>&1 \
        || fail "lake exe cache get failed twice (see $lg)"
    fi
    if [ -f "$dir/scripts/lake-cache-get.sh" ]; then
      ensure_space 60 "the published olean cache of $id"
      local rt=$tmp_root/$id
      mkdir -p "$rt" "$dir/.lake/cache"
      artifact_env=(LAKE_CACHE_DIR="$dir/.lake/cache" LAKE_ARTIFACT_CACHE=true LAKE_RESTORE_ARTIFACTS=true)
      step "scripts/lake-cache-get.sh (the library's own olean cache)"
      heavy RUNNER_TEMP="$rt" "${artifact_env[@]}" bash "$dir/scripts/lake-cache-get.sh" "$dir" >>"$lg" 2>&1 \
        || fail "scripts/lake-cache-get.sh failed (see $lg)"
      artifact_env+=(LAKE_NO_CACHE=true)
    fi
  fi
  if [ "$relation" = mathlib ] && [ "$built" = 0 ]; then
    step "check that Mathlib needs no compiling (lake build --no-build Mathlib)"
    if ! heavy "${artifact_env[@]}" lake build --no-build Mathlib >>"$lg" 2>&1; then
      [ "$ALLOW_MATHLIB_BUILD" = 1 ] || fail "Mathlib is not fully restored in this workspace; refusing to compile it (ALLOW_MATHLIB_BUILD=1 overrides)"
      log "  $id: Mathlib will be compiled (ALLOW_MATHLIB_BUILD=1)"
    fi
  fi

  # 5. build
  if [ "$built" = 0 ]; then
    ensure_space 10 "the build of $id"
    set_state stage=building
    step "lake build ${targets[*]} (LEAN_NUM_THREADS=$LEAN_THREADS, timeout ${OPT_BUILD_TIMEOUT}s)"
    heavy "${artifact_env[@]}" timeout --kill-after=120 "$OPT_BUILD_TIMEOUT" lake build "${targets[@]}" >>"$lg" 2>&1 \
      || fail "lake build ${targets[*]} failed or timed out (see $lg)"
  fi
  if [ -n "$donor" ]; then
    # The proof that sharing is safe: no file the donor sees changed since the link, and the
    # recipient still holds the donor's inodes.
    local name touched
    for name in "${link_names[@]}"; do
      touched=$(find "$donor/.lake/packages/$name" -newer "$marker" -type f -print -quit)
      if [ -n "$touched" ]; then
        touch "$link_off"
        log "  WARNING $id: $touched changed in place, a file shared with the donor; linking is now disabled"
        set_state "note=shared write: $touched"
      fi
      [ "$(probe_inode "$dir/.lake/packages/$name")" = "$(probe_inode "$donor/.lake/packages/$name")" ] \
        || fail "the linked package $name was replaced during the build"
    done
    set_state "unshared_family_files=$(find "${link_names[@]/#/$dir/.lake/packages/}" -type f -links 1 | wc -l)" \
      "donor_untouched=$([ -e "$link_off" ] && echo 0 || echo 1)"
    rm -f "$marker"
  fi
  set_state stage=built
  if [ "$relation" = mathlib ] && [ "$shared" = 0 ]; then
    step "register as a hard-link donor"
    py add-donor --checkout "$dir" --donors "$donors" --label "$id@${commit:0:12}" >>"$lg" 2>&1 \
      || log "  $id: not registered as a donor (see $lg)"
  fi

  # 6. probe
  ensure_space 3 "the probe of $id"
  set_state stage=probing
  step "probe_host.sh SHARD_COUNT=$OPT_SHARD_COUNT ($(wc -l <"$roots_file") root modules)"
  # SRC_DIRS must be passed by name: cenv is `env -i`, and without it the probe resolves
  # module paths at the checkout root, so a library whose sources sit under SRC_DIR was
  # indexed with every path null.
  cenv SHARD_COUNT="$OPT_SHARD_COUNT" ${OPT_MAX_PARALLEL:+MAX_PARALLEL="$OPT_MAX_PARALLEL"} \
    MODULE_PREFIXES="@$prefixes_file" SRC_DIRS="$OPT_SRC_DIR" PROBE_NICE="$NICE_LEVEL" \
    timeout --kill-after=120 "$OPT_PROBE_TIMEOUT" bash "$probe_host" "$dir" "$id" "@$roots_file" "$out" \
    >>"$lg" 2>&1 || fail "probe_host.sh failed (see $lg and $out/work/)"
  py write-entry --universe "$universe" --id "$id" --commit "$commit" --out-dir "$out" \
    "checkout=$dir" "linked_from=$donor" "toolchain=$toolchain" 2>>"$lg" \
    || fail "could not write $out/universe-entry.json"
}

# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------
log "build_universe: $universe (${#all_ids[@]} libraries${only_ids[*]:+; only ${only_ids[*]}}), root $ATLAS_ROOT, floor ${MIN_FREE_GB}G, $(avail_gb)G free"
failed=0 done_n=0 stopped=0
for id in "${all_ids[@]}"; do
  if [ "${#only_ids[@]}" -gt 0 ] && ! printf '%s\n' "${only_ids[@]}" | grep -qxF -- "$id"; then
    continue
  fi
  ( process_library "$id" ); rc=$?
  py summary --universe "$universe" --state-dir "$state_dir" >"$state_dir/status.tsv.tmp" 2>/dev/null \
    && mv -f "$state_dir/status.tsv.tmp" "$state_dir/status.tsv"
  case $rc in
    0) done_n=$((done_n + 1)) ;;
    3) stopped=1; break ;;
    *) failed=$((failed + 1)) ;;
  esac
done
log "build_universe: finished: $done_n done, $failed failed$([ "$stopped" = 1 ] && echo ', STOPPED at the disk floor'); $(avail_gb)G free; status in $state_dir/status.tsv"
[ "$stopped" = 1 ] && exit 3
[ "$failed" -gt 0 ] && exit 1
exit 0
