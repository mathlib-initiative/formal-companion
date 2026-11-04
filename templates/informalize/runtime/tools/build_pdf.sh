#!/bin/sh
# Build the informal text reproducibly into OUTDIR.
#
#   bash informal/tools/build_pdf.sh [--only N[,M...]] [--clean] [--source-commit SHA] OUTDIR
#
# Part of formal-companion's informalize runtime (docs/proposals/informalize.md,
# section 7.3, in the formal-companion skill); ported from the pilot's
# build_pdf.sh.  Apache License 2.0.
#
# Run it from the output repository's root, the directory that holds informal/.
# It copies informal/ into OUTDIR/src/ and builds the copy there, so an edit
# made during the build cannot leave latexmk out of passes; every file of the
# build is written under OUTDIR, so several builds run at once, each into its
# own OUTDIR.  The main file, the job name and SOURCE_DATE_EPOCH come from
# informal/informalize.json; OUTDIR/<job>.pdf is the PDF.
#
# Each build replaces OUTDIR/src/ and OUTDIR/runtime/, so OUTDIR is refused
# when it lies inside informal/, when it is the current directory or holds it,
# and when it exists, is not empty and holds no build-only.txt or
# build-receipt.json of an earlier build.  A second build into an OUTDIR that
# a build is still using is refused too: OUTDIR/.build-lock is held while the
# copy and latexmk run.
#
# The marker package is this script's own: the fcmarkers.sty beside tools/ is
# copied to OUTDIR/runtime/, which comes first on TEXINPUTS.  A gate that
# builds a contribution with its own copy of the runtime therefore builds it
# with its own marker package, whatever informal/ holds.
#
# Two clean builds of the same sources give byte-identical PDFs: the dates come
# from SOURCE_DATE_EPOCH with FORCE_SOURCE_DATE=1, and the code run before the
# main file sets \pdftrailerid{} and \pdfsuppressptexinfo=-1.  latexmk runs
# with -norc (no latexmkrc is read), -no-shell-escape and -recorder, and with
# openin_any=p and openout_any=p, so the source may not name an absolute path,
# a parent directory or a dot file.  A symbolic link anywhere in informal/ is
# refused.
#
#   --only 2,3          typeset sections 2 and 3 only; the others are skipped
#                       but counted, so no number changes, and references into
#                       them print ??.  Numbers are read as numbers (02 is 2),
#                       and one that sections.tex does not list is refused
#   --clean             delete what an earlier build left in OUTDIR first
#   --source-commit SHA record the output repository commit that informal/
#                       is, in the receipt and then in markers.json
#
# It writes OUTDIR/<job>.pdf, .aux, .log, .fls and OUTDIR/build-receipt.json
# (the PDF's sha256 and size, the pdfTeX and latexmk versions and the sha256 of
# every file TeX read), and prints the PDF's sha256 last.  Exit status: 0 built,
# 1 LaTeX failed (its error messages are printed, without the source lines TeX
# shows around them), 2 a usage or configuration error, 3 a program or a TeX
# file is missing.
set -eu

me=build_pdf.sh
die() { echo "$me: $2" >&2; exit "$1"; }
usage() { die 2 "usage: $me [--only N[,M...]] [--clean] [--source-commit SHA] OUTDIR"; }

only= clean=0 commit= outdir=
while [ $# -gt 0 ]; do
  case $1 in
    --only) [ $# -ge 2 ] || usage; only=$2; shift 2 ;;
    --only=*) only=${1#--only=}; shift ;;
    --clean) clean=1; shift ;;
    --source-commit) [ $# -ge 2 ] || usage; commit=$2; shift 2 ;;
    --source-commit=*) commit=${1#--source-commit=}; shift ;;
    -h|--help) sed -n '2,/^set -eu/p' "$0" | sed 's/^# \{0,1\}//; /^set -eu/d'; exit 0 ;;
    -*) usage ;;
    *) [ -z "$outdir" ] || usage; outdir=$1; shift ;;
  esac
done
[ -n "$outdir" ] || usage

only=$(printf '%s' "$only" | tr -d ' ')
case $only in
  *[!0-9,]*|,*|*,|*,,*) die 2 "--only takes a comma list of section numbers, not '$only'" ;;
esac
# Section numbers as numbers: 02 is section 2, as fcmarkers.sty reads it.
numbers=
for n in $(printf '%s' "$only" | tr ',' ' '); do
  n=${n#"${n%%[!0]*}"}
  [ -n "$n" ] || die 2 "--only: there is no section 0"
  numbers=${numbers:+$numbers,}$n
done
only=$numbers
case $commit in
  '') ;;
  *[!0-9a-f]*) die 2 "--source-commit takes a 40-hex commit" ;;
  *) [ ${#commit} -eq 40 ] || die 2 "--source-commit takes a 40-hex commit" ;;
esac

tools=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
runtime=$(dirname -- "$tools")
[ -f "$runtime/fcmarkers.sty" ] || die 2 "no fcmarkers.sty beside $tools"
[ -d informal ] || die 2 "no informal/ here; run me from the output repository's root"
informal=$(CDPATH='' cd -- informal && pwd -P)

for program in python3 pdflatex latexmk kpsewhich; do
  command -v "$program" >/dev/null 2>&1 || die 3 "$program is not on PATH"
done

link=$(find "$informal" -type l -print | head -n 1)
[ -z "$link" ] || die 2 "informal/ holds a symbolic link, which a build never follows: $link"

# The job, the main file, the time and the TeX files, one per line.
config=$(python3 "$tools/fccommon.py" config "$informal") || exit 2
job=$(printf '%s\n' "$config" | sed -n 1p)
main=$(printf '%s\n' "$config" | sed -n 2p)
epoch=$(printf '%s\n' "$config" | sed -n 3p)
packages=$(printf '%s\n' "$config" | sed -n '4,$p')

missing=
for name in $packages; do
  kpsewhich "$name" >/dev/null 2>&1 || missing="$missing $name"
done
[ -z "$missing" ] || die 3 "TeX cannot find:$missing"

made=0
[ -d "$outdir" ] || made=1
mkdir -p -- "$outdir"
outdir=$(CDPATH='' cd -- "$outdir" && pwd -P)
here=$(pwd -P)
refuse() {
  [ "$made" = 0 ] || rmdir -- "$outdir" 2>/dev/null || true
  die 2 "$1"
}
case $outdir/ in
  "$informal"/*) refuse "OUTDIR may not be inside informal/" ;;
esac
case $here/ in
  "$outdir"/*) refuse "OUTDIR may not be the current directory or hold it: $outdir" ;;
esac
case $outdir in
  *:*) refuse "OUTDIR may not hold a colon (it goes on TEXINPUTS)" ;;
esac
if [ "$made" = 0 ] && [ ! -f "$outdir/build-only.txt" ] && [ ! -f "$outdir/build-receipt.json" ] \
    && [ -n "$(ls -A -- "$outdir")" ]; then
  die 2 "OUTDIR $outdir is not empty and holds no earlier build; give a new directory"
fi

# One build at a time in OUTDIR.
lock="$outdir/.build-lock"
mkdir -- "$lock" 2>/dev/null \
  || die 2 "another build is using $outdir (remove $lock if none is running)"
trap 'rmdir -- "$lock" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$clean" = 1 ]; then
  rm -rf -- "$outdir/src" "$outdir/runtime" "$outdir/proof-sheets"
  rm -f -- "$outdir/$job".* "$outdir/build-only.txt" "$outdir/build-receipt.json" \
    "$outdir/latexmk.out" "$outdir/markers.json"
fi

# A stale .fdb_latexmk from a build of other sections must not make latexmk
# skip a pass, so the sections of the last build in OUTDIR are recorded.
stamp="$outdir/build-only.txt"
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$only" ]; then
  rm -f -- "$outdir/$job.aux" "$outdir/$job.fdb_latexmk" "$outdir/$job.out"
fi
printf '%s' "$only" > "$stamp"

rm -rf -- "$outdir/src" "$outdir/runtime"
mkdir -p -- "$outdir/src" "$outdir/runtime"
cp -R -- "$informal/." "$outdir/src/"
cp -- "$runtime/fcmarkers.sty" "$outdir/runtime/fcmarkers.sty"

SOURCE_DATE_EPOCH=$epoch
FORCE_SOURCE_DATE=1
openin_any=p
openout_any=p
TEXINPUTS="$outdir/runtime:"
max_print_line=1000     # whole lines in the log, so an error message is never cut
export SOURCE_DATE_EPOCH FORCE_SOURCE_DATE openin_any openout_any TEXINPUTS max_print_line

pretex='\pdftrailerid{}\pdfsuppressptexinfo=-1\relax'
if [ -n "$only" ]; then
  pretex="$pretex\\def\\FCOnly{$only}"
fi

cd -- "$outdir/src"
if ! latexmk -norc -pdf -recorder -no-shell-escape -interaction=nonstopmode \
    -halt-on-error -file-line-error -jobname="$job" -outdir="$outdir" \
    -auxdir="$outdir" "-usepretex=$pretex" "$main" >"$outdir/latexmk.out" 2>&1; then
  echo "$me: LaTeX failed; see $outdir/$job.log" >&2
  grep -E '^[^ ]+:[0-9]+: |^! ' "$outdir/$job.log" 2>/dev/null | head -n 20 >&2 || true
  exit 1
fi
[ -f "$outdir/$job.pdf" ] || die 1 "latexmk wrote no $job.pdf; see $outdir/latexmk.out"

set -- receipt "$outdir" --only "$only"
[ -z "$commit" ] || set -- "$@" --source-commit "$commit"
python3 "$tools/fccommon.py" "$@" || exit 2

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$outdir/$job.pdf"
else
  shasum -a 256 "$outdir/$job.pdf"
fi
