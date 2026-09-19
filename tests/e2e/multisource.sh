#!/usr/bin/env bash
#
# e2e-multisource.sh -- rebuild the whole multi-source demonstration from nothing.
#
# Proves the two requirements the previous data model was rejected over.
#
#   R1  The unit of organisation is a COMPANION with a LIST OF SOURCES.  This
#       workspace ends up holding 4 companions and 3 sources.  One companion is
#       audited against TWO sources; one source is audited by exactly ONE
#       companion (so no differential is possible on it, and `cf pair` says so
#       and skips it); one source is a bundled REFERENCE edition audited by
#       nobody; and after two more companions are onboarded, a SECOND source
#       carries a differential of its own, in a tree that never touches the
#       first one's.
#
#   R2  NO MANUAL INPUT.  The two target companions are onboarded with NO FLAGS.
#       The source, the edition, the sha256, the page offset, the item kinds,
#       the errata page and which edition the repo's anchors follow are all
#       discovered from the repositories themselves.
#
# Read-only with respect to the companion repos and the skill.  Nothing is
# pushed and `lake` is never run.  The only network access is the errata fetch
# (curl, raw HTML kept verbatim), which --offline skips.
#
# usage:  e2e-multisource.sh [--offline] [<workspace dir>]

set -u

SKILL="${CF_SKILL:-/Users/jack/Desktop/companion-faithfulness}"
CF="python3 $SKILL/scripts/cf.py"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OFFLINE=""
WS=""
for arg in "$@"; do
  case "$arg" in
    --offline) OFFLINE="--no-errata" ;;
    *)         WS="$arg" ;;
  esac
done
WS="${WS:-$HERE/ws-test}"

TML="${CF_TML:-/Users/jack/Desktop/tao-measure-lean}"
MT="${CF_MT:-/Users/jack/Desktop/measure_theory}"
CORPUS="${CF_BASELINE_CORPUS:-/Users/jack/Desktop/tao-diff-run}"

PUB=tao-measure-gsm126-published
DRAFT=tao-measure-2011-draft
BOOK=elementary-sets
PUB_SHA=64498329b65eea0145b3dec77ace89bc9f34df82ed4f35dd7c467125c2a3dfec

FAILED=0
step  () { printf '\n\033[1m=== %s\033[0m\n' "$*"; }
run   () { printf '\n$ %s\n' "$*"; "$@"; }
check () {  # check <label> <actual> <expected>
  if [ "$2" = "$3" ]; then printf '  ok    %-54s %s\n' "$1" "$2"
  else printf '  FAIL  %-54s got [%s], want [%s]\n' "$1" "$2" "$3"; FAILED=$((FAILED+1)); fi
}

# Readers.  Every check below reads an ARTIFACT, never a log line, so a command
# that prints the right thing while writing the wrong thing still fails.
py () { python3 -c "$1" "${@:2}" 2>/dev/null; }
jget () {  # jget <file.json> <dotted.path>   -- prints len() of a list
  py 'import json,sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."):
    d = d[int(k)] if isinstance(d, list) else d[k]
print(len(d) if isinstance(d, (list, dict)) else d)' "$1" "$2"; }
yget () {  # yget <file.yaml> <dotted.path>   -- via the skill's own reader
  py 'import sys
sys.path.insert(0, sys.argv[3])
from cflib.yamlio import read_yaml
d = read_yaml(sys.argv[1])
for k in sys.argv[2].split("."):
    d = d[int(k)] if isinstance(d, list) else d[k]
print(len(d) if isinstance(d, (list, dict)) else d)' "$1" "$2" "$SKILL/scripts"; }
ws () {  # ws <python expression over `w`, the loaded Workspace>
  py "import sys; sys.path.insert(0, sys.argv[1])
from cflib.workspace import Workspace
w = Workspace.load('.')
print($1)" "$SKILL/scripts"; }
tally () {  # tally <repo> <dotted path under the group>
  py 'import json,sys
g = json.load(open("reports/tally.all.json"))["groups"]["%s::%s" % (sys.argv[1], sys.argv[2])]
for k in sys.argv[3].split("."):
    g = g[k]
print(len(g) if isinstance(g, (list, dict)) else g)' "$PUB" "$1" "$2"; }

step "0. preconditions"
for p in "$TML" "$MT"; do
  [ -d "$p" ] || { echo "missing companion repo: $p"; exit 2; }
done
command -v pdftotext >/dev/null || { echo "pdftotext (poppler) is required"; exit 2; }
printf '  skill       %s\n  workspace   %s\n  companions  %s\n              %s\n' \
  "$SKILL" "$WS" "$TML" "$MT"
[ -n "$OFFLINE" ] && echo "  (offline: the errata fetch is skipped)"

# ---------------------------------------------------------------------------
step "1. generate the synthetic second source and its two companions"
# A public-domain booklet written here from the stdlib, plus two Lean companions
# that formalise it.  Nothing is downloaded; no copyrighted text is copied.
PDF="$HERE/elementary-notes.pdf"
run python3 "$HERE/make_pdf.py" "$PDF" || exit 2
run python3 "$HERE/make_notes_repos.py" "$HERE" "$PDF" || exit 2
echo "  booklet sha $(shasum -a 256 "$PDF" | cut -c1-12)"

# ---------------------------------------------------------------------------
step "2. cf init -- a workspace with no sources and no companions"
rm -rf "$WS"
run $CF init "$WS" || exit 2
cd "$WS" || exit 2
check "sources at birth"    "$(ws 'len(w.sources)')"         "0"
check "companions at birth" "$(ws 'len(w.companion_names())')" "0"

# ---------------------------------------------------------------------------
step "3. cf onboard -- NO FLAGS AT ALL (R2)"
run $CF onboard "$TML" $OFFLINE || exit 2
run $CF onboard "$MT"  $OFFLINE || exit 2

step "4. what discovery worked out with nothing typed in"
check "both companions resolved the SAME source" \
      "$(ws "','.join(sorted(w.shared_sources(min_companions=2)))")" "$PUB"
check "the source is identified by its content (sha256)" \
      "$(yget workspace.yaml "sources.$PUB.sha256")" "$PUB_SHA"
check "the PDF was located on this machine" \
      "$(test -n "$(yget workspace.yaml "sources.$PUB.pdf")" && echo found)" "found"
check "page offset, inferred from the running heads" \
      "$(jget "sources/$PUB/meta.json" offset)" "16"
check "  by which method" \
      "$(jget "sources/$PUB/meta.json" offset_method)" "running_head_mode"
check "  at what confidence (>= 0.99)" \
      "$(py "print('yes' if float('$(jget "sources/$PUB/meta.json" offset_confidence)') >= 0.99 else 'no')")" "yes"
check "item kinds, inferred from the text" \
      "$(jget "sources/$PUB/meta.json" kinds)" "8"
check "the bundled 2011 draft is a REFERENCE, audited by nobody" \
      "$(yget workspace.yaml "sources.$DRAFT.role")" "reference"
check "  and so no companion lists it" \
      "$(ws "len(w.companions_of('$DRAFT'))")" "0"
check "anchor edition, decided by scoring the repo's own anchors" \
      "$(yget workspace.yaml "companions.tao-measure-lean.anchor_source")" "$PUB"
if [ -z "$OFFLINE" ]; then
  check "errata page, found and fetched raw" \
        "$(jget "sources/$PUB/errata.json" http_status)" "200"
fi

# ---------------------------------------------------------------------------
step "5. the pinned regression numbers, on the live workspace"
check "chapter-1 items in the source" \
      "$(py 'import json
d = json.load(open("sources/'"$PUB"'/items.json"))
print(len([r for r in d["items"] if r.get("chapter") == 1]))')" "411"
check "  including Exercise 1.3.19" \
      "$(py 'import json
d = json.load(open("sources/'"$PUB"'/items.json"))
print("yes" if any(r["item"] == "Exercise 1.3.19" for r in d["items"]) else "no")')" "yes"

# The audited scope is INFERRED from the items the companions tag, and they now
# tag chapter-2 items too, so it comes out [1, 2].  The pinned figures were
# measured over chapter 1; narrow the scope so the comparison is like for like
# rather than quietly reporting a different number against the same name.
run $CF sources set $PUB chapters=1
run $CF pair || exit 2
check "items in scope"             "$(jget "pairs/$PUB/reconciliation.json" items_in_scope)" "411"
check "differential task universe" "$(jget "pairs/$PUB/reconciliation.json" task_universe)"  "383"

# ---------------------------------------------------------------------------
step "6. plan every preset"
# Both companions' conventions.md are the shipped template, so nothing has been
# confirmed by an owner: planning over an unconfirmed convention list is refused
# and the override is recorded in run.yaml.
$CF plan-run --preset differential-text --run-id x-unconfirmed -q >/dev/null 2>&1
check "plan-run refuses unconfirmed conventions" "$?" "2"
for P in differential-text differential-pdf differential-gpt; do
  $CF plan-run --preset "$P" --run-id "p-$P" --conventions-unconfirmed -q >/dev/null \
    || FAILED=$((FAILED+1))
  check "plan-run $P" "$(yget "runs/p-$P/run.yaml" tasks.count)" "383"
done
for P in attack-vac attack-hunt; do
  $CF plan-run --preset "$P" --companion tao-measure-lean --run-id "p-$P" \
    --conventions-unconfirmed -q >/dev/null || FAILED=$((FAILED+1))
  check "plan-run $P" "$(yget "runs/p-$P/run.yaml" tasks.count)" "506"
done
# --chapters narrows an ATTACK universe too (it used to be accepted and ignored),
# and the narrowing is recorded.  No figure is pinned here: the assertion is that
# the narrowed run is smaller than the full one and not empty.
$CF plan-run --preset attack-hunt --companion tao-measure-lean --run-id p-ch1 \
  --chapters 1 --conventions-unconfirmed -q >/dev/null || FAILED=$((FAILED+1))
check "plan-run --chapters narrows the attack universe" \
      "$(py 'import sys
sys.path.insert(0, sys.argv[1])
from cflib.yamlio import read_yaml
full = read_yaml("runs/p-attack-hunt/run.yaml")["tasks"]["count"]
narrow = read_yaml("runs/p-ch1/run.yaml")["tasks"]["count"]
print("narrower" if 0 < narrow < full else "%s vs %s" % (narrow, full))' "$SKILL/scripts")" "narrower"
# The VALUE, not the length: `yget` prints len() for a list, so a run narrowed to
# chapter 9 would also have printed "1" here.
check "  and run.yaml records which chapters" \
      "$(py 'import sys
sys.path.insert(0, sys.argv[1])
from cflib.yamlio import read_yaml
print(",".join(str(c) for c in read_yaml("runs/p-ch1/run.yaml")["tasks"]["chapters"]))' "$SKILL/scripts")" "1"
# the other three presets are gate-only scoring configs, not runnable plans
for P in differential-legacy attack-legacy mechanical; do
  $CF plan-run --preset "$P" --run-id "x-$P" -q >/dev/null 2>&1
  check "plan-run refuses gate-only $P" "$?" "2"
done

# ---------------------------------------------------------------------------
step "7. import the five baseline runs (2,672 findings)"
if [ -d "$CORPUS" ]; then
  # A v1 finding records no source.  This workspace holds more than one, so the
  # import REFUSES to guess which edition they read -- that is the gate, not a
  # nuisance, and batch 1 is exactly the corpus that read a different edition.
  $CF import-corpus --run-id guess --from "$CORPUS/findings" --no-tally -q >/dev/null 2>&1
  check "import refuses to guess the edition" "$?" "2"

  import_one () {  # <run-id> <dir> <mode> <preset> <expected>
    $CF import-corpus --run-id "$1" --from "$CORPUS/$2" --mode "$3" --preset "$4" \
       --source "$PUB" --repos ours,teorth,adam --no-tally -q >/dev/null \
       || FAILED=$((FAILED+1))
    check "import $1" "$(jget "corpus/$1/RECEIPT.json" actual.count)" "$5"
    check "  complete, every file accounted for" \
          "$(jget "corpus/$1/RECEIPT.json" complete)" "True"
  }
  import_one batch1     findings      differential differential-text 382
  import_one batch-gpt  findings-gpt  differential differential-gpt  382
  import_one batch-pdf  findings-pdf  differential differential-pdf  382
  import_one batch-hunt findings-hunt attack       attack-hunt       763
  import_one batch-vac  findings-vac  attack       attack-vac        763
  check "  2,672 findings in all" \
        "$(py 'import json,glob
print(sum(json.load(open(p))["actual"]["count"]
          for p in glob.glob("corpus/batch*/RECEIPT.json")))')" "2672"

  step "8. tally -- the pinned finding numbers"
  run $CF tally -q >/dev/null || FAILED=$((FAILED+1))
  check "ours rows"                  "$(tally ours   rows)"               "72"
  check "ours items"                 "$(tally ours   raw.items)"          "40"
  check "ours substantive"           "$(tally ours   raw.substantive)"    "17"
  check "ours high"                  "$(tally ours   raw.high)"           "3"
  # 41/19, not 40/18, since in-text labels stopped collapsing onto the numbered
  # item they merely cite: findings-hunt's "In-text definition, Exercise 1.4.24"
  # is the in-text definition, not Exercise 1.4.24.  A de-conflation, not a new
  # finding (the `ours` numbers above are unchanged).
  check "teorth substantive (collapsed to book items)" \
                                     "$(tally teorth collapsed.substantive)" "41"
  check "teorth high (collapsed to book items)" \
                                     "$(tally teorth collapsed.high)"        "19"
  check "adam rows"                  "$(tally adam   rows)"               "7"
  check "adam items"                 "$(tally adam   raw.items)"          "5"
  check "adam high"                  "$(tally adam   raw.high)"           "1"
else
  echo "  (no baseline corpus at $CORPUS -- skipping steps 7 and 8)"
fi

# ---------------------------------------------------------------------------
step "9. detect-proxies, route, report"
run $CF detect-proxies tao-measure-lean -q >/dev/null || FAILED=$((FAILED+1))
DC="pairs/$PUB/def-coverage.tao-measure-lean.json"
check "def-coverage stated"      "$(jget "$DC" stated)"      "32"
check "def-coverage bridge-only" "$(jget "$DC" bridge_only)" "4"
check "def-coverage missing"     "$(jget "$DC" missing)"     "0"

run $CF route -q >/dev/null || FAILED=$((FAILED+1))
run $CF report -q >/dev/null || FAILED=$((FAILED+1))
check "the handoff opens with a per-source table" \
      "$(py 'import glob
lines = open(sorted(glob.glob("reports/handoff.*.md"))[0]).read().splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("| source | sha256 |"))
rows = []
for l in lines[start + 2:]:
    if not l.startswith("|"):
        break
    rows.append(l.split("|")[1].strip())
print(",".join(sorted(rows)))')" \
      "\`$DRAFT\`,\`$PUB\`"

step "10. the gates still refuse"
$CF run p-differential-text --host evil.example.com --i-have-consent -q >/dev/null 2>&1
check "run refuses a non-allowlisted host" "$?" "2"
$CF run p-differential-text --host ffmain -q >/dev/null 2>&1
check "run refuses a server run without consent" "$?" "2"
rm -f emitted.json
$CF route --companion tao-measure-lean --emit-upstream emitted.json -q >/dev/null 2>&1
check "unchallenged findings are not exported" \
      "$(test -f emitted.json && echo wrote || echo refused)" "refused"

# ---------------------------------------------------------------------------
step "11. R1 -- a SECOND source, attached to ONE companion"
run $CF sources add "$PDF" --for tao-measure-lean \
    --title "Elementary Notes on Sets" --edition "generated fixture" || FAILED=$((FAILED+1))
check "a brand-new source is unconfirmed in the workspace overlay" \
      "$(yget registry.yaml "sources.$BOOK.confirmed")" "False"
check "its offset was inferred too, from its own running heads" \
      "$(jget "sources/$BOOK/meta.json" offset)" "2"
check "  at confidence 1.00" \
      "$(jget "sources/$BOOK/meta.json" offset_confidence)" "1.0"
check "tao-measure-lean is now audited against TWO sources" \
      "$(ws "len(w.companion('tao-measure-lean').sources)")" "2"
check "the booklet is audited by exactly ONE companion" \
      "$(ws "len(w.companions_of('$BOOK'))")" "1"
run $CF status

step "12. R1 -- one companion means no differential, and cf pair says so"
run $CF pair || FAILED=$((FAILED+1))
check "still exactly one source with a differential" \
      "$(ws 'len(w.shared_sources(min_companions=2))')" "1"
check "the booklet got no pairs tree" \
      "$(test -d "pairs/$BOOK" && echo yes || echo no)" "no"
check "the Tao tree is untouched" \
      "$(jget "pairs/$PUB/reconciliation.json" task_universe)" "383"

# ---------------------------------------------------------------------------
step "12b. R2/R3 -- a source NOBODY has catalogued, onboarded with no flags"
# Every onboarding above resolved through the shipped registry or through an
# overlay row `cf sources add` had already written. This is the other path: a
# brand-new workspace meeting a book no registry knows, where the only things
# joining "the work" (formalization.yaml) to "the file" (sources/*.pdf) are the
# title and the bytes. It used to register ONE PDF as TWO sources and then
# refuse, leaving a workspace.yaml its own loader rejected -- after which no cf
# command in that directory could run at all.
VIRGIN="$WS.virgin"
rm -rf "$VIRGIN"
run $CF init "$VIRGIN" --no-git -q || FAILED=$((FAILED+1))
( cd "$VIRGIN" || exit 2
  $CF onboard "$HERE/notes-a" --no-errata -q >/dev/null 2>&1
  echo "$?" > onboard.rc )
check "onboard of an uncatalogued source succeeds" "$(cat "$VIRGIN/onboard.rc")" "0"
check "  one PDF is ONE source"  "$(cd "$VIRGIN" && ws 'len(w.sources)')" "1"
check "  the workspace it wrote still loads" \
      "$(cd "$VIRGIN" && $CF status -q >/dev/null 2>&1 && echo loads || echo BROKEN)" "loads"
check "  the overlay remembers the sha it fingerprinted" \
      "$(yget "$VIRGIN/registry.yaml" "sources.anonymous-elementary-sets.sha256" | cut -c1-8)" \
      "0de15de4"
check "  and the offset it inferred" \
      "$(yget "$VIRGIN/registry.yaml" "sources.anonymous-elementary-sets.offset")" "2"
check "  and the item kinds it read off the text" \
      "$(test "$(yget "$VIRGIN/registry.yaml" "sources.anonymous-elementary-sets.kinds")" -gt 0 \
         && echo yes)" "yes"
check "  still unconfirmed: nobody has checked it" \
      "$(yget "$VIRGIN/registry.yaml" "sources.anonymous-elementary-sets.confirmed")" "False"
check "  and cf sources remove undoes it" \
      "$(cd "$VIRGIN" && $CF sources remove anonymous-elementary-sets -q >/dev/null 2>&1 \
         && ws 'len(w.sources)')" "0"

# ---------------------------------------------------------------------------
step "13. R1 -- two more companions, discovered flaglessly, share the booklet"
run $CF onboard "$HERE/notes-a" --no-errata || FAILED=$((FAILED+1))
run $CF onboard "$HERE/notes-b" --no-errata || FAILED=$((FAILED+1))
check "the booklet resolved by SHA to the source already registered" \
      "$(ws 'len(w.sources)')" "3"
check "companions in the workspace" "$(ws 'len(w.companion_names())')" "4"
check "the booklet is now audited by three companions" \
      "$(ws "','.join(sorted(w.companions_of('$BOOK')))")" "notes-a,notes-b,tao-measure-lean"
check "TWO sources now carry a differential" \
      "$(ws 'len(w.shared_sources(min_companions=2))')" "2"

step "14. R1 -- cf pair over a workspace with two paired sources"
run $CF pair || FAILED=$((FAILED+1))
check "the Tao tree is STILL untouched" \
      "$(jget "pairs/$PUB/reconciliation.json" task_universe)" "383"
check "the booklet now has its own tree" \
      "$(test -d "pairs/$BOOK" && echo yes || echo no)" "yes"
check "every row in the booklet's tree names the booklet" \
      "$(py 'import json
rows = [json.loads(l) for l in open("pairs/'"$BOOK"'/pairs.jsonl") if l.strip()]
ids = {r["source_id"] for r in rows if "_meta" not in r}
print(",".join(sorted(ids)))')" "$BOOK"
check "no Tao item leaked into the booklet tree" \
      "$(py 'import json
rows = [json.loads(l) for l in open("pairs/'"$BOOK"'/pairs.jsonl") if l.strip()]
items = {r.get("item", "") for r in rows if "_meta" not in r}
print("LEAKED" if any(i.startswith(("Exercise 1.6", "Definition 1.7")) for i in items) else "clean")')" "clean"
check "and no booklet item leaked into the Tao tree" \
      "$(py 'import json
rows = [json.loads(l) for l in open("pairs/'"$PUB"'/pairs.jsonl") if l.strip()]
ids = {r["source_id"] for r in rows if "_meta" not in r}
print(",".join(sorted(ids)))')" "$PUB"

step "15. R1 -- the finished shape"
run $CF status
run $CF sources list

# ---------------------------------------------------------------------------
step "16. result"
if [ "$FAILED" -eq 0 ]; then
  printf '\n  \033[32mALL CHECKS PASSED\033[0m   workspace: %s\n\n' "$WS"
else
  printf '\n  \033[31m%d CHECK(S) FAILED\033[0m   workspace: %s\n\n' "$FAILED" "$WS"
fi
exit "$FAILED"
