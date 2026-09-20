#!/usr/bin/env bash
#
# synthetic.sh -- the whole flow on synthetic data, runnable by anyone.
#
# No private checkouts, no network, no `lake`, nothing typed in: it writes a
# public-domain booklet with the stdlib, writes two Lean companions of it that
# carry EIGHT planted defects (tests/e2e/answer-key.json is the ground truth),
# onboards both with no flags, pairs them, runs the model-free detector and
# scores what it found against the key.
#
# That last step is the only quality number in this repo a stranger can
# reproduce: precision and recall of the free detector over a fixture whose
# defects are known.  It is not a measurement of any real companion.
#
# The only external requirement is poppler's `pdftotext`.  Without it the script
# says so and exits 0, unless CF_REQUIRE_POPPLER=1 (what CI sets).
#
# usage:  synthetic.sh [--fixtures <dir>] [<workspace dir>]
#         both default to a fresh temp directory, which the last line names.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The skill is two directories up from tests/e2e -- derived, never a path from
# the machine this was written on.
SKILL="${CF_SKILL:-$(cd "$HERE/../.." && pwd)}"
CF="python3 $SKILL/scripts/cf.py"
KEY="$HERE/answer-key.json"
STAGES="$HERE/stages.anonymous-elementary-sets.json"

FIX=""
WS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --fixtures) FIX="$2"; shift 2 ;;
    *)          WS="$1"; shift ;;
  esac
done
SCRATCH=""
if [ -z "$FIX" ] || [ -z "$WS" ]; then
  SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/cf-synthetic.XXXXXX")"
fi
FIX="${FIX:-$SCRATCH}"
WS="${WS:-$SCRATCH/ws}"

BOOK=anonymous-elementary-sets
BOOK_SHA=0de15de43744e8a5d2193833bcc7d7fe78a1cb9bc0d85b22c2e7ba700dda668e

FAILED=0
step  () { printf '\n\033[1m=== %s\033[0m\n' "$*"; }
run   () { printf '\n$ %s\n' "$*"; "$@"; }
check () {  # check <label> <actual> <expected>
  if [ "$2" = "$3" ]; then printf '  ok    %-54s %s\n' "$1" "$2"
  else printf '  FAIL  %-54s got [%s], want [%s]\n' "$1" "$2" "$3"; FAILED=$((FAILED+1)); fi
}
# Every check reads an ARTIFACT, never a log line, so a command that prints the
# right thing while writing the wrong thing still fails.
py () { python3 -c "$1" "${@:2}" 2>/dev/null; }
sha256 () {  # shasum on macOS, sha256sum on most Linux images; neither is everywhere
  if command -v shasum >/dev/null; then shasum -a 256 "$1" | cut -d' ' -f1
  else sha256sum "$1" | cut -d' ' -f1; fi; }
jget () {  # jget <file.json> <dotted.path>  -- prints len() of a list or dict
  py 'import json,sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."):
    d = d[int(k)] if isinstance(d, list) else d[k]
print(len(d) if isinstance(d, (list, dict)) else d)' "$1" "$2"; }
yget () {  # yget <file.yaml> <dotted.path>  -- via the skill's own reader
  py 'import sys
sys.path.insert(0, sys.argv[3])
from cflib.yamlio import read_yaml
d = read_yaml(sys.argv[1])
for k in sys.argv[2].split("."):
    d = d[int(k)] if isinstance(d, list) else d[k]
print(len(d) if isinstance(d, (list, dict)) else d)' "$1" "$2" "$SKILL/scripts"; }

step "0. preconditions -- poppler only"
if ! command -v pdftotext >/dev/null; then
  if [ "${CF_REQUIRE_POPPLER:-0}" = "1" ]; then
    echo "  pdftotext (poppler) is required: install poppler-utils"; exit 2
  fi
  echo "  SKIP: pdftotext (poppler) is not on PATH, so the booklet cannot be read."
  echo "        Install it (brew install poppler / apt-get install -y poppler-utils)"
  echo "        and re-run: bash tests/e2e/synthetic.sh"
  exit 0
fi
printf '  skill       %s\n  fixtures    %s\n  workspace   %s\n' "$SKILL" "$FIX" "$WS"

# ---------------------------------------------------------------------------
step "1. write the booklet and two companions of it (8 planted defects)"
PDF="$FIX/elementary-notes.pdf"
run python3 "$HERE/make_pdf.py" "$PDF" || exit 2
run python3 "$HERE/make_notes_repos.py" "$FIX" "$PDF" || exit 2
check "the booklet is byte-identical to the pinned fixture" \
      "$(sha256 "$PDF")" "$BOOK_SHA"
check "the answer key plants 8 defects" \
      "$(py 'import json,sys
d = json.load(open(sys.argv[1]))
print(sum(1 for e in d["entries"] if e["expect"] == "defect"))' "$KEY")" "8"

# ---------------------------------------------------------------------------
step "2. cf init + cf onboard -- NO FLAGS: the source is discovered"
rm -rf "$WS"
run $CF init "$WS" --no-git || exit 2
cd "$WS" || exit 2
run $CF onboard "$FIX/notes-a" --no-errata || exit 2
run $CF onboard "$FIX/notes-b" --no-errata || exit 2
check "one PDF is ONE source"       "$(yget workspace.yaml sources)" "1"
check "  fingerprinted by content"  "$(yget registry.yaml "sources.$BOOK.sha256")" "$BOOK_SHA"
check "  page offset, inferred from the running heads" \
      "$(jget "sources/$BOOK/meta.json" offset)" "2"
check "  at confidence 1.00"        "$(jget "sources/$BOOK/meta.json" offset_confidence)" "1.0"
check "  by which method"           "$(jget "sources/$BOOK/meta.json" offset_method)" "running_head_mode"
check "items in the booklet"        "$(jget "sources/$BOOK/items.json" count)" "28"
check "companions, discovered flaglessly" \
      "$(yget workspace.yaml companions)" "2"
check "  both with the same adapter" \
      "$(yget workspace.yaml companions.notes-b.adapter)" "docstring_tags"

# ---------------------------------------------------------------------------
step "3. cf pair -- the differential universe and the anchors"
run $CF pair || exit 2
check "items in scope"              "$(jget "pairs/$BOOK/reconciliation.json" items_in_scope)" "28"
check "items both companions formalise" \
      "$(jget "pairs/$BOOK/reconciliation.json" pairs)" "17"
check "definitions notes-a states"  "$(jget "pairs/$BOOK/def-coverage.notes-a.json" stated)" "7"
check "definitions notes-b states"  "$(jget "pairs/$BOOK/def-coverage.notes-b.json" stated)" "6"
check "  and the one it never defines" \
      "$(jget "pairs/$BOOK/def-coverage.notes-b.json" missing)" "1"
# One planted wrong anchor and nothing else: every other page in the fixture is
# the page the booklet really prints the item on.
check "PAGE_ANCHOR findings"        "$(jget "pairs/$BOOK/anchor-check.json" summary.findings)" "1"
check "  none of them advisory"     "$(jget "pairs/$BOOK/anchor-check.json" summary.advisory)" "0"

# ---------------------------------------------------------------------------
step "4. the model-free detector"
for R in notes-a notes-b; do
  run $CF detect-proxies "$R" --stages "$STAGES" -q >/dev/null || FAILED=$((FAILED+1))
done
for R in notes-a notes-b; do
  check "body proxies found in $R" \
        "$(jget "pairs/$BOOK/def-coverage.$R.json" proxy_candidates)" "1"
done

# ---------------------------------------------------------------------------
step "5. cf score -- precision and recall against the planted defects"
run $CF score --run detect-notes-a-cheap --run detect-notes-b-cheap \
    --run "anchor-check-$BOOK" --answer-key "$KEY" || FAILED=$((FAILED+1))
for FIELD in precision recall f1; do
  check "recorded $FIELD of the free detector" \
        "$(jget reports/score.all.json "totals.$FIELD")" \
        "$(jget "$KEY" "recorded_scores.mechanical.$FIELD")"
done
check "planted defects it found"    "$(jget reports/score.all.json totals.true_positives)" "4"
check "  and false positives"       "$(jget reports/score.all.json totals.false_positives)" "0"
check "decoys reported"             "$(jget reports/score.all.json decoys_reported)" "0"
# The key judges the 18 items the companions formalise; the booklet prints 28.
# Nothing the mechanical tier emits lands on the other 10, and if something did
# it would be counted here rather than charged against precision.
check "reported outside the key"     "$(jget reports/score.all.json not_judged_reported)" "0"
# The measurement the repo had no instrument for: the four defects no mechanical
# rule can see are the four it misses, and the score says so out loud.
check "the kinds it cannot see are missed, not hidden" \
      "$(py 'import json
d = json.load(open("reports/score.all.json"))
print(",".join(sorted({m["kind"] for m in d["misses"]})))')" "CONTENT,VACUITY"

# ---------------------------------------------------------------------------
step "6. result"
if [ "$FAILED" -eq 0 ]; then
  printf '\n  \033[32mALL CHECKS PASSED\033[0m   workspace: %s\n\n' "$WS"
else
  printf '\n  \033[31m%d CHECK(S) FAILED\033[0m   workspace: %s\n\n' "$FAILED" "$WS"
fi
exit "$FAILED"
