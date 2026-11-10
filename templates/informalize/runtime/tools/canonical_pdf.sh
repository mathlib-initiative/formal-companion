#!/bin/sh
# Build the canonical PDF twice, check it, and write pdf-receipt.json.
#
#   bash informal/tools/canonical_pdf.sh [--source-commit SHA] WORKDIR
#
# Part of formal-companion's informalize runtime (docs/proposals/informalize.md,
# section 9, in the formal-companion skill); ported from the pilot's
# final-build steps.  Apache License 2.0.
#
# Run it from the output repository's root, the directory that holds informal/.
# On the run host that root is the copy `cf informalize pdf-command` rsyncs to
# WORKDIR/src, and WORKDIR is the folder beside it; nothing is written outside
# WORKDIR.  It runs no git: the commit the sources are comes in with
# --source-commit, and markers.json carries it on.
#
#   1. two clean builds, into WORKDIR/a and WORKDIR/b (logs build-a.log and
#      build-b.log beside them), refused unless the two PDFs have one sha256;
#   2. extract_markers.py a, check_items.py a --strict and proof_sheets.py a
#      --dest a/proof-sheets;
#   3. WORKDIR/pdf-receipt.json: the source commit, the PDF's sha256, bytes and
#      pages, the pdfTeX, latexmk and poppler versions, the inputs' sha256 from
#      the build receipt and each check's result;
#   4. WORKDIR/DONE, holding the exit status, written last -- on a failure too.
#
# Without poppler it stops after step 1 with exit 4, naming the three programs:
# the host is expected to have them.
#
# Exit status: 0 done; 2 a usage or configuration error; 3 a program or a TeX
# file is missing; 4 poppler is not on PATH; 5 the two builds differ; 6 a check
# of step 2 failed; 7 the receipt could not be written.
set -eu

me=canonical_pdf.sh
log() { printf '%s %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$me" "$*"; }
usage() { log "usage: $me [--source-commit SHA] WORKDIR" >&2; exit 2; }

commit= workdir=
while [ $# -gt 0 ]; do
  case $1 in
    --source-commit) [ $# -ge 2 ] || usage; commit=$2; shift 2 ;;
    --source-commit=*) commit=${1#--source-commit=}; shift ;;
    -h|--help) sed -n '2,/^set -eu/p' "$0" | sed 's/^# \{0,1\}//; /^set -eu/d'; exit 0 ;;
    -*) usage ;;
    *) [ -z "$workdir" ] || usage; workdir=$1; shift ;;
  esac
done
[ -n "$workdir" ] || usage
case $commit in
  '') ;;
  *[!0-9a-f]*) log "--source-commit takes a 40-hex commit" >&2; exit 2 ;;
  *) [ ${#commit} -eq 40 ] || { log "--source-commit takes a 40-hex commit" >&2; exit 2; } ;;
esac

tools=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
[ -d informal ] || { log "no informal/ here; run me from the output repository's root" >&2
                     exit 2; }
mkdir -p -- "$workdir"
workdir=$(CDPATH='' cd -- "$workdir" && pwd -P)

# DONE is the last thing written, on a failure too: fetch-pdf reads it first.
status=2
finish() { printf '%s\n' "$status" > "$workdir/DONE"; }
trap finish EXIT

say() { log "$*"; }
stop() { status=$1; shift; say "FAIL: $*"; exit "$status"; }

job=$(python3 "$tools/fccommon.py" config informal | sed -n 1p) || stop 2 "informalize.json"
[ -n "$job" ] || stop 2 "informalize.json names no job"

build() {
  where=$1
  say "build $where"
  rm -rf -- "$workdir/$where"
  set -- --clean
  [ -z "$commit" ] || set -- "$@" --source-commit "$commit"
  code=0
  bash "$tools/build_pdf.sh" "$@" "$workdir/$where" > "$workdir/build-$where.log" 2>&1 || code=$?
  [ "$code" = 0 ] || { tail -n 20 "$workdir/build-$where.log" >&2 || true
                       stop "$([ "$code" = 3 ] && echo 3 || echo 2)" \
                            "build $where exited $code; see $workdir/build-$where.log" ; }
}

sha_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

build a
build b
sha_a=$(sha_of "$workdir/a/$job.pdf")
sha_b=$(sha_of "$workdir/b/$job.pdf")
[ "$sha_a" = "$sha_b" ] || stop 5 "the two clean builds differ: $sha_a and $sha_b (the build is \
not reproducible on this machine; see $workdir/build-a.log and build-b.log)"
say "two clean builds agree: $sha_a"

missing=
for program in pdftotext pdftoppm pdfinfo; do
  command -v "$program" >/dev/null 2>&1 || missing="$missing $program"
done
[ -z "$missing" ] || stop 4 "poppler is not on PATH:$missing (the markers, the item checks \
and the proof sheets need pdftotext, pdftoppm and pdfinfo); the PDF is $workdir/a/$job.pdf"

check() {
  name=$1; shift
  say "$name"
  code=0
  "$@" > "$workdir/$name.log" 2>&1 || code=$?
  [ "$code" = 0 ] || { tail -n 20 "$workdir/$name.log" >&2 || true
                       stop 6 "$name exited $code; see $workdir/$name.log" ; }
}

check extract_markers python3 "$tools/extract_markers.py" "$workdir/a"
check check_items python3 "$tools/check_items.py" "$workdir/a" --strict
check proof_sheets python3 "$tools/proof_sheets.py" "$workdir/a" --dest "$workdir/a/proof-sheets"

set -- pdf-receipt "$workdir" --second-build "$workdir/b/$job.pdf"
[ -z "$commit" ] || set -- "$@" --source-commit "$commit"
for name in two_builds extract_markers check_items proof_sheets; do
  set -- "$@" --check "$name=ok"
done
python3 "$tools/fccommon.py" "$@" || stop 7 "the receipt could not be written"
say "wrote $workdir/pdf-receipt.json"
status=0
