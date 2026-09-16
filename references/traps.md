# Traps → fixes

Every entry below cost real work in the 2026-09 audit. Each is written as *how it presents* (the
symptom you will actually see), *the fix*, and *where the evidence is*. Most of them fail
**silently** or look like an unrelated failure, which is why they are worth a page.

Host names, server addresses, agentbox profile names and session ids deliberately do not appear
here; they belong in `campaign.yaml`.

---

## A. The book and its sources

**A1 — Two PDF editions, differing by a page.**
*Presents as:* a wave of page-anchor findings, or an adjudicator refuting anchors that look right.
*Cause:* the draft PDF and the published edition paginate differently — roughly 65 numbered items
sit one printed page earlier in the published book, nearly all in one section.
*Fix:* pin one edition by sha256 in `campaign.yaml`; stamp `book_sha` into every artifact; the gate
refuses a mismatch; `anchor_check` runs against the pinned edition only. Batch-1 anchor findings
were produced against the draft and had to be withdrawn wholesale.
*Evidence:* `/Users/jack/Desktop/tao-measure-lean/CLAUDE.md` lines 48-55;
`/Users/jack/Desktop/tao-differential/refuted.adam.jsonl` `_meta.official_pdf` and row 1.

**A2 — The printed/PDF offset.**
*Presents as:* an agent grepping the right page number in the wrong coordinate system, or a page
image request that returns the wrong page.
*Fix:* one offset field per book (`printed page = PDF page − 16` for the audited edition), applied
in exactly one place; page images are named by **PDF** page, zero-padded to three digits, and the
prompt says so.
*Evidence:* the run prompts' "You can SEE the page" block; `book.json.offset`.

**A3 — A summarised source is not the source.**
*Presents as:* "no erratum exists", confidently, when one does.
*Cause:* the local errata file was produced by a summarising fetch tool and had silently dropped
the entry that decided a high-severity finding.
*Fix:* `book/fetch_errata.py` uses `curl` against the live page, keeps the **raw HTML** beside the
rendered markdown, and dates both. Never use a summarising fetch for a source of record.
*Evidence:* `/Users/jack/Desktop/tao-measure-lean/docs/HANDOFF.md` §4;
`REVIEW_PATTERNS.md` P13.

**A4 — `pdftotext` destroys notation.**
*Presents as:* a whole class of defects invisible to a text-only run — bars, overlines,
subscripts, primes, hats, sup vs limsup.
*Fix:* render page images and tell the run to read the image whenever notation could decide the
question, while still quoting from the text file so the quote gate can verify it. That is exactly
what distinguishes the `-pdf` and `hunt` run variants from their text-only siblings.
*Evidence:* the "You can SEE the page" block, added to both the worker and reviewer prompts of
those two variants.

**A5 — The book text is copyrighted and must not spread.**
*Presents as:* a convenient copy of `book.txt`, page images or crops inside a repo.
*Fix:* book text, page images and crops live only in the campaign directory, gitignored; findings
quote 1-3 sentences and stay in the campaign repo; `--redact-quotes` replaces `book_quote` with
(page, normalised hash) for anything shared. The gate reads the book from a path outside every
repo and **fails closed** when it is absent — which presents as a run failure, not as a missing
file, so check that first.
*Evidence:* `~/Desktop/tao-diff-run/README.md`; `docs/HANDOFF.md` §7 ("DO NOT DELETE").

---

## B. Counting, harvesting, reporting

**B1 — The harvest was one file short.**
*Presents as:* every derived count off by one, in the "cleaner" direction.
*Fix:* `RECEIPT.json` with the source count and a sha manifest; `harvest` fails on a mismatch.
rsync completing is not proof of completeness.
*Evidence:* `docs/HANDOFF.md` §4 (762 local vs 763 on the server; the missing file was a real
`CONTENT` finding).

**B2 — The task generator dropped an item.**
*Presents as:* a denominator of 410 for a chapter with 411 numbered items, and one item formalised
by two repos with no task at all.
*Cause:* a single-space regex against text where the heading read `Exercise    1.3.19`.
*Fix:* whitespace-tolerant item parsing everywhere, and `make_tasks` reconciles the task count
against `items.json ∩ any-repo`, failing loudly on a gap.
*Evidence:* `memory/tao-differential-audit-verified-figures.md`.

**B3 — Triaging from what you read instead of what merged.**
*Presents as:* "everything is fixed" while three findings — one high — have never been adjudicated.
*Fix:* the work list is always re-derived mechanically from the merged corpus; "nothing open" ships
with the command that recomputes it.
*Evidence:* `docs/OPEN_FAITHFULNESS_FINDINGS.md` (three successive wrong counts, all toward
"cleaner"); `docs/HANDOFF.md` §4.

**B4 — A grep is not the authoritative count.**
*Presents as:* 523 `sorry` where the repo's own classifier says 516 (510 exercises + 6 gaps),
because 7 hits are prose.
*Fix:* call the repo's classifier when it has one; declare it in `adapter.yaml`.
*Evidence:* `memory/faithfulness-audit-method.md`; `tao-measure-lean/scripts/update_status_table.py`
`classify()`.

**B5 — Sub-parts inflate cross-repo counts.**
*Presents as:* 62 substantive items for a companion that actually has 40.
*Fix:* collapse sub-parts before quoting any item count; the raw count is a row count, not an item
count.
*Evidence:* `memory/tao-differential-audit-verified-figures.md`.

---

## C. The orchestrator (tend) and the bridge (agentbox)

**C1 — A strict schema returns good work to the queue, silently.**
*Presents as:* a task that did perfect work — committed, gate green, task marked complete — being
re-queued with no error anyone reads.
*Cause:* tend's result models are strict; the agent described `validation` as a sentence where the
schema wants a list of evidence objects.
*Fix:* `run/normalize_result.py` whitelists and coerces the agent's self-written result into the
strict contract, folding prose `validation` into `notes`, and downgrades `completed` to
`needs_review` when the underlying call did not succeed.
*Evidence:* `RUNBOOK.md` addendum ("Strict schemas fail good work quietly"); the shipped
normaliser.

**C2 — Spend is invisible when bridging through a hosted agent.**
*Presents as:* `aggregate usage: input=0, output=0` and a budget command that caps nothing.
*Fix:* treat the gateway/host as the only enforceable cap; record a `max_cost` and the billing path
in `run.yaml`; never claim tend's budget protects a bridged run.
*Evidence:* `RUNBOOK.md` addendum; `docs/HANDOFF.md` §8.

**C3 — The host caps concurrent sessions per account.**
*Presents as:* HTTP 409s once you pass 16 sessions — and the cap is per account, across campaigns,
so two campaigns cannot both run at full width.
*Fix:* `plan-run` writes worker+reviewer concurrency into `run.yaml` and refuses a total above the
configured cap; a second campaign waits.
*Evidence:* `docs/HANDOFF.md` §8.

**C4 — The worktree explosion.**
*Presents as:* 382 git worktrees and 1.1 GB appearing within minutes of starting a run, even with
one worker allowed, because tend creates a worktree per ready task.
*Fix:* budget disk before the run (tasks × worktree size), gate with `depends_on` or copy-on-write
directories, and clean up closed worktrees. They are recreated on demand; an orchestration root
with no `worktrees/` is its normal pre-run state.
*Evidence:* `RUNBOOK.md` NOTE 2026-09-07.

**C5 — The rework loop needs the reviewer's feedback.**
*Presents as:* a task looping forever, each attempt repeating the rejected mistake.
*Cause:* on rework tend returns the worktree to pending; a bridge that re-runs the original prompt
never shows the worker why it was rejected.
*Fix:* the bridge appends the tail of `.tend/discussion.md` under a "THIS IS A REVISION" heading.
*Evidence:* the comment in the shipped `bin/worker-agent.sh`.

---

## D. The local Workflow path

**D1 — Session limits kill a fleet mid-run.**
*Presents as:* dozens of agents failing at once, partway through: 129 of 225 adjudications in one
batch, 70 of 114 in another, verify votes lost in a review run.
*Fix:* size fleets by agent count, not item count; put subagents on `model: 'opus'`; treat resume
as the normal path — edit only the failed `agent()` options so unchanged calls stay cached, and
resume with `resumeFromRunId`; recover a killed run's results from `journal.jsonl`.
*Evidence:* `memory/model-budget-and-tool-quirks.md`;
`memory/tao-differential-audit-verified-figures.md` (batch 1 stopped at 129/225 on the session
limit, **not** on instruction); the recovered harvest-from-journal script.

**D2 — `args` must be actual JSON.**
*Presents as:* a workflow run that completes instantly and silently with zero agents.
*Cause:* it was launched with `[]`.
*Fix:* build `args` with a script that prints the JSON, and assert a non-zero length in the script's
first `log()`.
*Evidence:* `/Users/jack/Desktop/notes/camera-ready-HANDOFF-2026-09-08.md` §6.

**D3 — A Workflow script has no filesystem.**
*Presents as:* a design that says "run the gate inside the script after each agent" and cannot.
*Fix:* worker self-gating plus a post-hoc `harvest_journal → verify → re-queue` second run. The
local path has no analogue of the orchestrator's request-changes loop.
*Evidence:* the batch-1 fleet, whose quote check ran at harvest time.

**D4 — `scriptPath` is rejected after the cwd moves.**
*Presents as:* the tool refusing the path it returned, once the session cwd has moved between
worktrees.
*Fix:* copy the script into the session scratchpad and pass that path.
*Evidence:* `memory/model-budget-and-tool-quirks.md`.

**D5 — Do not use a clock in a workflow script.**
*Presents as:* a resume that re-runs everything because `Date.now()` changed the prompt bytes.
*Fix:* all inputs via `args`; replay is keyed on prompt bytes and call-prefix order.
*Evidence:* `references/workflow-patterns.md` §0 rule 3; `memory/model-budget-and-tool-quirks.md`.

---

## E. Shell, Python, and small tools

**E1 — zsh does not word-split `$VAR`.**
*Presents as:* one long argument where several flags were intended, and a tool complaining about an
unknown option.
*Fix:* write the flags out, or use an array.
*Evidence:* `notes/camera-ready-HANDOFF-2026-09-08.md` §6.

**E2 — `pathlib.with_suffix` eats a dotted name.**
*Presents as:* a crop or output file for item `1.7.1` written as `1.7.png`, overwriting its
sibling.
*Fix:* build such paths by string concatenation.
*Evidence:* same source.

**E3 — The framework Python and absolute paths.**
*Presents as:* a syntax error on modern type annotations, or a tool run with the wrong interpreter,
after `PATH="/usr/bin:$PATH"` promotes the system Python.
*Fix:* for the repo that needs it, call the framework interpreter **by absolute path**; keep the
PATH change (some tooling rejects Homebrew's symlinked git/node); clear `__pycache__` before tests
that check for a dirty tree.
*Evidence:* `notes/measure-theory-HANDOFF.md` "Environment quirks";
`adam-detector/README.md` (`repin.py` requires the framework interpreter).

**E4 — A local HTTP server sends no cache headers.**
*Presents as:* a stale page surviving a plain reload for hours while you debug the wrong thing.
*Fix:* hard-reload, or serve with cache headers.
*Evidence:* `notes/camera-ready-HANDOFF-2026-09-08.md` §6.

---

## F. Lean, Lake and Verso

**F1 — Lake replays cached logs.**
*Presents as:* a green build that proves nothing; a replayed success masked a broken module for
hours.
*Fix:* delete the `.olean` (or the module's artifacts) and force a recompile before claiming any
module is green.
*Evidence:* `memory/tao-measure-lean-project.md`; `memory/tao-measure-companion-campaign.md`.

**F2 — Verso docstrings are parsed as markup.**
*Presents as:* a build error inside a docstring: a bare `*` opens emphasis; `[of]` inside a quoted
sentence becomes a link target.
*Fix:* escape them (`` `m*` ``, `\[of\]`). Where the repo's ids embed line numbers, keep docstring
edits **within their line count** or re-run the whole pin cascade.
*Evidence:* `notes/camera-ready-HANDOFF-2026-09-08.md` §6;
`memory/camera-ready-pass-2026-09-07.md`.

**F3 — A bare `lake build` can thrash a laptop.**
*Presents as:* an hour stalled, tens of GB of swap, from narrative/doc workers.
*Fix:* build the library target only (`lake build <Lib>`); never build a narrative layer in CI on a
hosted runner. Also: killing `lake` leaves orphaned `lean` children holding mmapped artifacts —
check for them after any interrupted build.
*Evidence:* `notes/camera-ready-HANDOFF-2026-09-08.md` §6; `docs/HANDOFF.md` §7;
`memory/camera-ready-pass-2026-09-07.md`.

**F4 — Generated layers are not covered by CI.**
*Presents as:* a generated table or blueprint drifting behind the Lean across many sections.
*Fix:* regenerate after any declaration change, as part of the fix stage's invariants.
*Evidence:* `docs/HANDOFF.md` §7.

**F5 — This Lake has no jobs flag.**
*Presents as:* `-j`/`--jobs` rejected.
*Fix:* plain `lake build`. Worktrees of the same project build instantly if you copy `.lake/build`
and symlink `.lake/packages` — lake trusts the copied traces.
*Evidence:* `memory/model-budget-and-tool-quirks.md`; `memory/measure-theory-repo-review.md`.

---

## G. Repos, worktrees and permissions

**G1 — Worktrees share one stash stack.**
*Presents as:* a stash pushed in one worktree appearing to another session, or work vanishing from
under someone else.
*Fix:* never `git stash` in a shared repo's worktrees; the campaign rule is to not touch another
session's repo or worktrees at all.
*Evidence:* `memory/measure-theory-faithfulness-audit.md` ("shared stash stack").

**G2 — The permission classifier blocks batched writes.**
*Presents as:* a loop of `gh pr merge` calls, or a write to an ssh config, refused mid-script.
*Fix:* one command per merge; hand genuinely blocked writes back to the user rather than working
around them. This is a feature, not an obstacle: the same classifier is what keeps an audit session
from writing to a peer's repo.
*Evidence:* `notes/measure-theory-HANDOFF.md` (merge order, "one command per merge");
`memory/ff-core-cloudflare-server.md`.

**G3 — The gate must run out of tree.**
*Presents as:* "every merged file passed the gate twice" as an assurance claim — while the gate
script lives inside the entrypoint the worker has write access to, and both gate invocations run
from that copy.
*Fix:* invoke the gate from the campaign/skill copy, pass the entrypoint as an argument, and check
`changed files ⊆ {findings/<id>.json, tasks/<id>.yaml}` before merge. Mount or copy source repos
read-only rather than relying on the prompt's "READ-ONLY" instruction.
*Evidence:* the tend config's two `validation` commands, both
`python3 scripts/verify_findings.py` relative to the worktree; design critique
`critique.json` gap 3.

**G4 — `git add <deleted-path>` aborts the whole add.**
*Presents as:* a staging step that quietly stages nothing.
*Fix:* stage deletions with `git rm` or `git add -A`.
*Evidence:* `memory/camera-ready-pass-2026-09-07.md`.

**G5 — Companion repos are private.**
*Presents as:* a convenient example in a skill file that names a private repo's conventions, a
host, or a session id.
*Fix:* skill examples are synthetic; repo paths, hosts, profiles, owners and session ids live in
`campaign.yaml` / `adapter.yaml` only; the campaign repo has no remote by default and a pre-push
guard, because "no remote" is a default and not a control.
*Evidence:* `CLAUDE.md` lines 69-81 (nothing published without checking); `critique.json` risks 1-3.
