# Traps → fixes

Every entry below cost real work in the 2026-09 audit. Each is written as *how it presents* (the
symptom you will actually see), *the fix*, and *where the evidence is*. Most of them fail
**silently** or look like an unrelated failure, which is why they are worth a page.

Host names, server addresses, agentbox profile names and session ids deliberately do not appear
here; they belong in `workspace.yaml`.

**Contents** — **A** sources, editions and discovery (read before your first run on a new
book) · **B** counting, harvesting, reporting · **C** the orchestrator and the bridge ·
**D** the local Workflow path · **E** shell, Python and small tools · **F** Lean, Lake and
Verso · **G** repos, worktrees and permissions.

---

## A. Sources, editions and discovery

**A1 — The anchor edition is not the bundled edition.**
*Presents as:* a wave of page-anchor findings, or an adjudicator refuting anchors that look right;
and, once discovery exists, a repo whose committed PDF is confidently adopted as the thing to audit
against.
*Cause:* the draft PDF and the published edition paginate differently — roughly 65 numbered items
sit one printed page earlier in the published book, nearly all in one section — and a repo may
**ship one and anchor to the other**. `tao-measure-lean` commits the 2011 draft and writes its
docstring anchors against the published book.
*Fix:* decide the anchor edition **mechanically**, by scoring the repo's own `page_anchor` rows
against each candidate's `items.json` (`agreement = ok / (ok + mismatched)`), accept only at ≥0.80
with a ≥0.05 margin, and let prose corroborate rather than decide. Record the bundled-but-not-
anchored edition in `bundled_sources` with `role: reference` so it is prepared for page-map
checking and audited by nobody. Stamp `source_id` + `source_sha` into every artifact; the gate
refuses a mismatch. Measured 2026-09-21: published 0.91 (271 ok / 27 mismatched) vs draft 0.68
(202 / 95).
*Evidence:* the docstring-tag companion's own `CLAUDE.md`, "anchor edition" section;
the v1 differential's `refuted.adam.jsonl` `_meta.official_pdf` and row 1; the
`anchor edition` block `cf onboard` prints.

**A1b — Both editions share the offset, so the error is exactly one page.**
*Presents as:* anchors that are "nearly right", which reads as sloppy formalisation rather than as
a wrong edition.
*Cause:* both Tao PDFs satisfy `pdf_page = printed_page + 16`; the editions differ only by the
one-page shift of ~65 items. A wrong-edition anchor is therefore never off by 16 — the size that
would be obvious — but by 1.
*Fix:* never diagnose an anchor mismatch by eye. Run the mechanical check against both candidates
and compare the agreement rates; the shape of the histogram, not the size of the error, is what
identifies the edition. The registry's `page_map_to` records the shift and which items it applies
to, in both directions, and a validator checks the two directions agree.

**A2 — The printed/PDF offset, and the confidence on it.**
*Presents as:* an agent grepping the right page number in the wrong coordinate system, or a page
image request that returns the wrong page.
*Fix:* one offset per source (`printed page = PDF page − offset`), inferred once from the running
heads and applied in exactly one place; page images are named by **PDF** page, zero-padded to three
digits, and the prompt says so.
*And the discovery half:* `offset_confidence < 0.9` means **ask**, never guess
(`cflib.inference.MIN_OFFSET_CONFIDENCE`). A healthy inference looks like the two editions measured
on this machine — 265 pages each, 245 voting pages with 244 agreeing on 16 (0.9959) and 249 voting
with 248 agreeing (0.9960). A guessed 16 at 0.71 would produce exactly the class of defect the
audit exists to find, so the command refuses and names `cf sources set <id> offset=N`, which stamps
`offset_method: manual` so a reader can tell an inference from a claim.
*The case with no offset at all:* a source that is not a paginated document (a `.tex` manuscript,
notes in Markdown, an HTML chapter) has no printed page, so it carries `pagination: none`,
`offset_method: none` and `locator: section`, and every page-dependent feature refuses on it by
name — `offset=`, `--images`, a page bind — instead of computing from a 0 that nothing supports
(`scripts/sources/locator.py`).
*Evidence:* the run prompts' "You can SEE the page" block; `sources/<id>/meta.json`
`offset_histogram` and `offset_voting_pages`.

**A2b — A truncated sha in prose is a candidate, not an identity.**
*Presents as:* a source adopted on the strength of `64498329…` in a `CLAUDE.md`, and a file that
was never fingerprinted being treated as the pinned edition.
*Fix:* an 8–63 hex prefix is stored as `sha256_prefix` and resolves only through a registry alias
or a real fingerprint of real bytes. Unresolved prefixes are *reported* — in the discovery result
and in `cf status` — never adopted. A file in the right place with the right name that fingerprints
to a different edition is an `edition_mismatch` warning, not a source.
*Evidence:* `cflib/discovery.py` module docstring, rules 1 and 2.

**A2c — The declared path is stale.**
*Presents as:* `pdf_found_in: repo_declared` for bytes the repo never actually declared.
*Cause:* `measure_theory`'s ledger declares `sources/gsm-126-tao5-measure-book.pdf`; that path does
not exist, and the bytes live in `.lake/official-pdf/`.
*Fix:* basename matching inside the repo ranks as `repo_cache`, one step **below** the paths the
repo names. Calling found-by-basename bytes "declared" hides exactly the fact a reader of the
provenance most needs.

**A3 — A summarised source is not the source.**
*Presents as:* "no erratum exists", confidently, when one does.
*Cause:* the local errata file was produced by a summarising fetch tool and had silently dropped
the entry that decided a high-severity finding.
*Fix:* `sources/fetch_errata.py` uses `curl` against the live page, keeps the **raw HTML** beside
the rendered markdown, and dates both in `errata.json` (URL, HTTP status, both shas, byte count,
entry count, `changed`). Never use a summarising fetch for a source of record. After the fetch the
raw HTML is checked for the word "errata"; absent, the confidence is halved and
`meta.json.warnings` records `errata_url_unconfirmed` — a page that does not mention errata is a
guess, however plausible its URL.
*Evidence:* the docstring-tag companion's `HANDOFF.md` §4 and its
`REVIEW_PATTERNS.md` P13.

**A4 — `pdftotext` destroys notation.**
*Presents as:* a whole class of defects invisible to a text-only run — bars, overlines,
subscripts, primes, hats, sup vs limsup.
*Fix:* render page images and tell the run to read the image whenever notation could decide the
question, while still quoting from the text file so the quote gate can verify it. That is exactly
what distinguishes the `-pdf` and `hunt` run variants from their text-only siblings.
*Evidence:* the "You can SEE the page" block, added to both the worker and reviewer prompts of
those two variants.

**A5 — The source text is copyrighted and must not spread.**
*Presents as:* a convenient copy of `text.txt`, page images or crops inside a repo.
*Fix:* source text, page images and crops live only in the workspace's gitignored `sources/`,
never in the skill repo or a companion; findings quote 1-3 sentences and stay in the workspace;
what leaves is an export with the quotes redacted to a hash. Two of them, and they hash the same
normalised sentence: `cf route --redact-quotes` replaces `book_quote` with (raw length, sha256) in
one companion's upstream report, and `cf share export` replaces it with (page, normalised length,
sha256, and up to a quarter of the quote's words from each end, none at all below fourteen words)
for a whole campaign — from which `cf quotes hydrate` recovers the sentence on a machine that
already holds that edition, and nowhere else. The
registry holds metadata and URLs only, which is what makes *it* committable. A download happens
only with `--download` **and** a licence note saying the author publishes the file himself, and
nothing is ever redistributed. The gate reads the text from a path outside every repo and **fails
closed** when it is absent — which presents as a run failure, not as a missing file, so check that
first.
*Evidence:* the harvested corpus's own README; `tao-measure-lean/docs/HANDOFF.md` §7 ("DO NOT DELETE");
`registry/README.md`.

**A6 — `sources/` in `.gitignore` hides `scripts/sources/`.**
*Presents as:* the entire stage-1 package silently absent from the next `git add`, and a clone that
cannot prepare a source at all.
*Cause:* the skill repo's own `.gitignore` carries the **workspace** patterns so that a stray copy
of workspace data cannot be committed by accident. `sources/` matches a directory at any depth, and
the code package is `scripts/sources/`.
*Fix:* the `!scripts/sources/` negation, which must move in the same commit as anything that
renames either side. Two lines, one meaning; they are commented in `.gitignore` as such.
*Evidence:* `.gitignore` in this repo; `docs/design/archive/multi-source-model.md` §6.3.

---

## B. Counting, harvesting, reporting

**B1 — The harvest was one file short.**
*Presents as:* every derived count off by one, in the "cleaner" direction.
*Fix:* `RECEIPT.json` with the source count and a sha manifest; `harvest` fails on a mismatch.
rsync completing is not proof of completeness.
*Evidence:* `tao-measure-lean/docs/HANDOFF.md` §4 (762 local vs 763 on the server; the missing file was a real
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
*Evidence:* `tao-measure-lean/docs/OPEN_FAITHFULNESS_FINDINGS.md` (three successive wrong counts, all toward
"cleaner"); `tao-measure-lean/docs/HANDOFF.md` §4.

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
*Evidence:* `RUNBOOK.md` addendum; `tao-measure-lean/docs/HANDOFF.md` §8.

**C3 — The host caps concurrent sessions per account.**
*Presents as:* HTTP 409s once you pass 16 sessions — and the cap is per account, across workspaces,
so two workspaces cannot both run at full width.
*Fix:* `plan-run` writes worker+reviewer concurrency into `run.yaml`, and `cf run` refuses a total
above `defaults.max_concurrent_sessions` (16) unless `--over-session-cap` is passed; the plan prints
`<n> sessions, cap <m>` so the number is visible before anything starts. A second workspace waits.
*Evidence:* `tao-measure-lean/docs/HANDOFF.md` §8.

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
*Evidence:* the camera-ready handoff note of the v1 campaign, §6.

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
*Evidence:* `notes/camera-ready-HANDOFF-2026-09-08.md` §6; `tao-measure-lean/docs/HANDOFF.md` §7;
`memory/camera-ready-pass-2026-09-07.md`.

**F4 — Generated layers are not covered by CI.**
*Presents as:* a generated table or blueprint drifting behind the Lean across many sections.
*Fix:* regenerate after any declaration change, as part of the fix stage's invariants.
*Evidence:* `tao-measure-lean/docs/HANDOFF.md` §7.

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
*Fix:* never `git stash` in a shared repo's worktrees; the standing rule is to not touch another
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
*Fix:* invoke the gate from the workspace/skill copy, pass the entrypoint as an argument, and check
`changed files ⊆ {findings/<id>.json, tasks/<id>.yaml}` before merge. Mount or copy source repos
read-only rather than relying on the prompt's "READ-ONLY" instruction.
*Evidence:* the tend config's two `validation` commands, both
`python3 <entrypoint>/scripts/verify_findings.py` relative to the worktree;
the design critique, gap 3.

**G4 — `git add <deleted-path>` aborts the whole add.**
*Presents as:* a staging step that quietly stages nothing.
*Fix:* stage deletions with `git rm` or `git add -A`.
*Evidence:* `memory/camera-ready-pass-2026-09-07.md`.

**G5 — Companion repos are private.**
*Presents as:* a convenient example in a skill file that names a private repo's conventions, a
host, or a session id.
*Fix:* skill examples are synthetic; repo paths, hosts, profiles, owners and session ids live in
`workspace.yaml` / `adapter.yaml` only; the workspace itself stays local, and what makes it stay
local is that `cf init` REFUSES a workspace repo with a remote unless `policy.allow_remote` says
otherwise. The pre-push hook is a reminder on top of that, not the control: `git push --no-verify`
skips it, a fresh clone does not carry it, and a remote added after `cf init` never passes through
it — which is why `cf status` prints, every run, whether there is a remote and whether the hook is
still in place. `cf init --no-remote-guard` adds the one guard a bare `git push --no-verify` does
not get past (`remote.pushDefault` to a remote that does not exist, `push.default nothing`, both in
that repo's local config); an explicit `git push <remote> <ref>` still works, so it is a guard and
not the control either. Staying local is not the same as being unshareable: `cf share export`
writes the campaign's conclusions — ledger rows, routed findings, tallies, receipts, source
metadata — with every quote redacted, and `cf share import` merges such a bundle into another
workspace without overwriting a verdict, which is how two people work one campaign.
*Evidence:* `CLAUDE.md` §1 (what may live here: source metadata and nothing else) and `POLICY.md`
§3 (never redistributed); the design critique, risks 1-3.
