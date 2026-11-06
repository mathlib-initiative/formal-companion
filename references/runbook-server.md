# Server runbook — orchestrator + hosted-agent bridge

How a fleet of hundreds of read-heavy tasks actually runs, distilled from the five run roots of the
2026-09 audit (three differential variants, two attack variants) and the RUNBOOK addendum written
after the run moved to the server. Written as: **what `cf` automates**, **what you check**, and
**what stays manual**.

No host address, account or profile name appears in this file. They live in `workspace.yaml`, and
`cf run` refuses any host not in its allowlist.

**Contents** — §0 the two tools this needs · §1 the topology · §2 preconditions ·
§3 `cf plan-run` · §4 `cf run` · §5 monitoring · §6 failure modes · §7 `cf harvest` ·
§8 the local fallback · §9 a locate root · §10 an informalization, from `init`
to publish · §11 what stays manual · §12 sources.

---

## 0. The two tools this needs, and where they come from

Everything in this file depends on two programs that **do not ship with this skill**:

- **`tend`** — the orchestrator. It owns a sqlite state database, a worktree per task, the
  retry/merge policy and the validation hooks. `cf plan-run` writes a *tend root* (a `config.yaml`,
  `.tend/*.yaml` and a `bin/` of bridges) for it; `cf run` copies that root to the host and starts
  it. Roughly 4,400 lines under `scripts/plan/` and `scripts/run/` exist to produce and drive it.
- **`agentbox`** — the hosted-session runner the bridges shell out to
  (`agentbox host claude --profile <profile> -- -p … --output-format json`, or
  `agentbox host codex … exec -m <model>`). It is what turns a rendered prompt into an agent turn.

**Where they come from: not recorded here.** Neither tool is vendored, pinned, installed or
version-checked by anything in this repository, and the repo does not record the version the 2026-09
runs used. Do not infer one from this file. The only version contract that exists is the one *you*
write: put a spec in your own `workspace.yaml` as `hosts.<host>.tend_version` (for example
`">=0.4,<0.6"`), and `cf run`'s preflight compares it against `tend --version` on the host and
refuses the run on a mismatch. With no spec recorded, preflight only prints the version and
continues — which is how a config-schema change becomes a run that validates locally and fails on
the host with no diagnosis. Every tend command runs as `cd <tend_dir> && uv run …`, with
`tend_dir` from `hosts.<host>.tend_dir` (default `$HOME/tend`, whatever `remote_root` is); set
`hosts.<host>.tend_cmd` instead to replace the whole prefix.

**Without them**, the supported path is the local Workflow fleet (§8) for anything under ~100
items, plus every mechanical stage — `pair`, `detect-proxies`, the anchor check, `import-corpus`,
`tally`, `route`, `report` — which needs neither tool and produced the whole def-coverage baseline.

**`cf run --execute` has driven this pair from this repo on one private host, for differential runs
and locate's smoke runs** (`docs/status.md`): the five runs that produced the baseline corpus pre-date the code that
drives them. Treat a first server run as a smoke test (§4.4), not as a resumption.

---

## 1. The topology

```
tend (orchestrator)                       entrypoint repo (git, main)
  ├─ worker agent ──► bridge ──► hosted Claude/codex session
  │                                   └─ writes findings/<task-id>.json + task yaml, commits
  ├─ validation gate  (mechanical, OUT OF TREE)
  ├─ reviewer agent ──► bridge ──► hosted session   (refute, not rubber-stamp)
  └─ pre-merge gate  → merge into main
```

- The orchestrator owns state (a sqlite database), worktrees, retries and merging. Its pipeline
  **worker → validation → reviewer → merge** maps exactly onto the audit's
  *probe → prove the quote is real → adversarially challenge → accept*.
- The **bridge scripts are the agent runtime**: `bin/worker-agent.sh` and `bin/reviewer-agent.sh`
  read the rendered prompts, shell out to a hosted session (`agentbox host claude --profile
  <profile> -- -p … --output-format json`, or `agentbox host codex … exec -m <model>` for the
  second model in a calibration pair), and pipe the result through `normalize_result.py`. This is
  legitimate under the orchestrator's contract, which accepts any executable that follows the
  stdout JSON contract.
- The five audited configs differ in exactly four places: **entrypoint path**, **gate `KINDS`**,
  **prompt add-ons** (page images; "no citation findings" + witness required), **bridge**
  (Claude vs codex/GPT), and **concurrency** (8/5 for the first differential, 6/4 for the image and
  GPT variants, 5/3 for both attack runs). Everything else — validation commands, merge target,
  batched merge, worktree cleanup — was identical. That is why a run is a *preset* in `run.yaml`
  rather than a new tree.

## 2. Preconditions — all manual, none automated

1. **ssh access to the host**, and the host listed in `workspace.yaml`'s allowlist.
2. **The hosted-agent profile is logged in on the server.** `cf` cannot log it in, and a stale login
   fails every task identically.
3. **The box owner's consent for a heavy job.** The audited box was shared; heavy jobs were
   coordinated by asking first. `cf run` requires `--i-have-consent` and records who consented, when
   and for what, in `run.yaml`. Without the flag it refuses.
4. **Source text and errata on the host, outside every repo** (copyrighted, and the gate reads
   them): `sources/<source_id>/` per source the run touches, transferred read-only.
   Page images too, for an image-enabled run.
5. **Source repos cloned read-only** on the host. "READ-ONLY" in a prompt is an instruction, not a
   control: copy or mount them read-only.
6. **Disk budget.** The orchestrator creates a worktree per ready task as soon as it is ready:
   382 worktrees took 1.1 GB within minutes even with one worker allowed. Budget
   `tasks × worktree size`, or gate with `depends_on` / copy-on-write copies.
7. **A billing decision.** When agents run inside a hosted session the orchestrator's usage counters
   read zero by construction and its budget command cannot cap the run; the only enforceable cap is
   gateway-side. Decide and record it before raising concurrency.
8. **git 2.31 or later on the host.** The one version floor this skill has. The gate blanks the
   content-filter drivers a worktree names, and a driver whose own name carries an `=` is reachable
   only through `GIT_CONFIG_COUNT`, which arrived in 2.31 — `git -c` splits its argument at the
   first `=`. Below that there is no channel left, so the gate stops (exit 3, "the gate could not
   run") rather than execute the program a worker named, and every task blocks. Debian 11 ships
   2.30.2. `cf doctor` reports the version and warns below the floor.

## 3. `cf plan-run` — what it writes

```
cf plan-run --preset attack-vac --companion ours --host <alias> --i-have-consent
```

- `runs/<run-id>/run.yaml` — mode, companion set, model ids, book access, gate mode, host alias,
  concurrency, consent record, billing path, task count, prompt shas.
- `runs/<run-id>/tasks/*.yaml` — one per item, title prefixed `DIFFERENTIAL:` / `SINGLE-SOURCE:` /
  `hunt:` / `vac:`; reconciled against `items.json ∩ any-repo` (`references/findings-schema-v2.md` §2).
- `runs/<run-id>/prompts/*.md` — worker-system, worker, worker-revision, reviewer-system, reviewer,
  rendered from `templates/prompts/` with the companion's `conventions.md` injected.
- `runs/<run-id>/gate.json` — `KINDS`, `VERDICTS`, witness requirement, a `sources` block
  (`{<source_id>: {text, sha256, text_sha256, page_offset, images_dir, item_pages}}`) and the
  allowed repo prefixes. The gate picks the text by the document's `source_id`; the two paths the
  legacy gate hard-wired (the book file and the repo-name regex) are fields here.
- a tend root — `config.yaml`, `.tend/*.yaml`, `bin/` (the two bridges, the normaliser, the
  out-of-tree gate invocation).

Then it runs `tend-task verify` over the tasks and stops if anything fails.

## 4. `cf run` — the launch sequence

What it does, in order — and each step is one you can also do by hand:

1. Refuse unless the host is in the allowlist and `--i-have-consent` is present.
2. `rsync` the entrypoint (tasks, scripts, data tables), the tend root and the prompts to the host.
   Book text, errata and page images go to a **separate directory outside every repo**.
3. Commit the tasks to the entrypoint's `main` — the orchestrator reads tasks from git.
4. **Smoke test 1/1.** Set limits to one worker and one reviewer, start the run, and watch exactly
   one task through worker → gate → reviewer → merge. Do not launch hundreds of tasks blind: model
   ids that have never been exercised from this machine fail immediately and repeatedly.
5. **Read that task's gate output once per tend version** — the validation command's, not the
   worker's or the reviewer's own. Its last line is `changed files: …`, and the same line is the
   `ownership` field of that call's `gate-log/gate.jsonl` record, beside `task_id_from` (where the
   id came from: `--task-id`, `$TEND_TASK_ID` or `orchestrator.sqlite`), `changed_files` (true
   for a validation or pre-merge call) and `pre_merge` (true for the pre-merge call alone). An
   agent runs with `TEND_TASK_ID` in its environment, so the worker's and the reviewer's own gate
   calls log the task id from `$TEND_TASK_ID`: a non-empty `task_id` in the log is **not** by
   itself evidence that the validation command found it, and only a `--changed-files` record says
   how the validation found it. `ownership checked against
   task <id> (task id from $TEND_TASK_ID)` on a validation call means the orchestrator exported
   `TEND_TASK_ID` into the validation command's environment; `(task id recovered from
   orchestrator.sqlite: …)` means it did not and the gate found the id in tend's database; either
   way the full ownership rule is running. `ownership: single-stem
   rule` means neither: only the weaker rule (one task's findings/task pair per contribution) is
   in force, so a worker that rewrites a neighbour's landed findings file inside its own single
   stem is not caught. **Whether tend exports it has never been verified here**, and it is a
   property of the orchestrator version, not of the run — so check it again after an upgrade. To
   restore the full rule, append `--task-id <the id>` to both gate commands in the tend root's
   `config.yaml` by whatever substitution that version supports; an argv list is not shell-expanded,
   so a literal `$TEND_TASK_ID` element makes the gate refuse every run. With `batched_merge: true`
   a pre-merge change set carries one findings/task pair per batched task — the findings file
   added, the task file modified, because step 3 above committed the tasks to the merge target. In
   a findings root (`verify_findings.py`) the same argv serves `validation_commands` and
   `pre_merge_validation_commands`, and the stem count only refuses several stems when some stem's
   findings file was already on the merge target, so a batch of new findings merges and a
   rewritten neighbour is still named. A locate root's `pre_merge_validation_commands` runs the
   same argv plus `--pre-merge`: only with that flag does `verify_locate.py` admit several stems
   (every document a stem carries must be new, so a stem whose findings were already on the merge
   target is refused, and so is one that brings no document); without it, as in every
   `validation_commands` call, a change set of several stems is refused. A 1/1
   smoke test cannot exercise that
   — there is nothing to batch at limits 1/1 — so read the pre-merge gate output again after step 6.
6. Only then raise the limits to the preset's concurrency.

```bash
# the manual equivalents, for when you are debugging rather than driving
tend-control limits --root <root> --workers 1 --reviewers 1
tend run --root <root> --log-level INFO
tend-control limits --root <root> --workers 8 --reviewers 5
```

**Set the budget ceiling before raising concurrency.** Each task runs a worker and at least one
reviewer, and a `request_changes` sends the task back for another worker turn, so the task count is
a lower bound on agent count, never a total.

## 5. `cf status` — monitoring

```bash
tend-control status --root <root> | grep -E 'tasks:|worktrees:|active_agents'
ls <entrypoint>/findings | wc -l        # merged findings = real progress
tail -f <orchestrator log>
```

`cf status` wraps these and writes the counts into `companions/<name>/state.json`. Steering:
`tend-control limits` (throttle), `pause` (stop new cost, keep state), `drain` (finish in flight,
start nothing), `stop`.

**Expected shape.** ~10–15 minutes per task through worker → gate → review → merge, because the
agents genuinely read the book and the Lean. At 8 workers that is ~8–12 h for 382 tasks and
~16–24 h for 763, plus rework loops. 13 concurrent agents left a 96-core box at load ~1 — the
limit is the hosted-session cap and the review loop, not CPU.

**Hard cap: 16 concurrent hosted sessions per account, across workspaces.** 8 workers + 5 reviewers
leaves three. Above the cap the API answers 409. The bridges now take one of 15 per-user `flock`
slots before every agent call (`CF_SLOT_DIR`, `CF_SLOTS`) and wait for a free one rather than
failing, so roots on one Unix user cannot together pass the cap; an empty or 409 envelope still
exits 75 so tend retries the same worktree.

## 6. Failure modes you will actually meet

| symptom | cause | response |
|---|---|---|
| a task completes perfectly and is silently re-queued | the agent's self-reported result did not match the orchestrator's strict schema | `normalize_result.py` (shipped) coerces it; check it is on the bridge path |
| a task loops, repeating a rejected mistake | the bridge re-ran the original prompt on rework | the shipped bridge appends the reviewer's feedback from `.tend/discussion.md` |
| the gate fails on every task | the book text is missing at the configured path | intended: the gate fails closed. Check the book directory before blaming the run |
| `aggregate usage: input=0` | agents run inside hosted sessions | expected; watch the gateway, not the orchestrator |
| 409s when starting | the session cap, reached from another Unix user or account | the bridge exits 75 and tend retries; lower concurrency, or wait for the other workspace |
| an agent call never returns | a hung session | every call runs under `timeout ${CF_AGENT_TIMEOUT:-45m}`; expiry exits 124, an infrastructure failure tend retries, never a verdict. A locate run whose task needs longer calls is planned with `cf plan-locate --agent-timeout <5m..3h>`, which the two wrappers export |
| the disk fills minutes after launch | one worktree per ready task | see §2.6 |
| a run dies | the orchestrator resumes from its sqlite state (`worker_running` → pending) | re-`tend run --root` with the same root; `run.yaml` records the exact command |

## 7. `cf harvest` — pulling results back

1. `rsync` the merged `findings/` from the entrypoint on the host into `corpus/<run-id>/`.
2. Write `RECEIPT.json`: origin host alias and path, expected count, actual count, sha256 manifest,
   `run.yaml` sha, and the run's `sources: {<id>: <sha>}`. **Fail on any mismatch.** A harvest once held 762 files where the
   host had 763 and the missing file was a real `CONTENT` finding.
3. Commit the corpus in the workspace repo (append-only per run-id).
4. Run `tally.py`, `union.py`, `agreed.py`; write the counts into `state.json`.
5. Price the run. `tend-control status` reports zero usage for every run, because the agents run
   inside Claude Code and the orchestrator never sees a token; the agents' own transcripts on the
   host carry every one (issue #11). The transcripts hold prompts, paths and tool output, so they
   stay on the host: run the standalone reader there, from a copy of `scripts/cost/usage.py`, as
   `python3 usage.py <dir> [<dir> ...] --export usage.jsonl` with every transcript directory of
   the run (one `{model, usage}` row per response, no text), bring the export back, and pass it to
   `cf harvest <run-id> --transcripts usage.jsonl`. The receipt then carries the `cost` block
   (tokens by model, dollars at `workspace.yaml` `budget.prices`, the models the table does not
   price, the table's sha) and `cf tally` prints the cost per task. A Codex fleet's
   `rollout-*.jsonl` logs read the same way; a log that names no model takes
   `--transcripts-model <id>`. A model missing from the price table is reported as UNPRICED,
   never guessed at, and the dollar figure is then a lower bound until the table has a row for it.

Everything downstream is a function of `corpus/`, never of files someone read.

## 8. Local fallback (< ~100 items)

A Workflow fleet on the laptop, per `references/workflow-patterns.md` §6. Two rules:

- The **default local bridge is `claude -p`**, not an API-key agent runtime: the 2026-09-05 attempt
  to run locally on an API key with no credit balance produced 20 requests, 18 retries and repeated
  HTTP 404/credit errors, which is what moved the run to the server in the first place.
- There is **no local analogue of the request-changes loop**. The worker self-gates; the real gate
  runs post-hoc at harvest — by default, since `cf harvest` gates unless `--no-gate` — and failures
  come back as a second run. On this path the harvest gate is the *only* independent check, which
  is why turning it off here is worse than turning it off on the server path, where
  `validation_commands` and `pre_merge_validation_commands` have already run it per task.

## 9. A locate root

A locate run (`cf plan-locate`; `docs/proposals/wikipedia-locate.md` §3, §6) uses the same tend
root, bridge and harvest, with these differences:

- **One task, its own directory.** A task writes `findings/<task-id>/<source-id>.json`, one
  document per source its manifest (`gate/tasks/<task-id>.json`) names, and changes nothing else
  but `tasks/<task-id>.yaml`. The gate is `gate/verify_locate.py`, invoked by tend's validation
  commands with `--changed-files --base <branch>`; it reads only the documents the change set
  touched and only the sources, candidates and manifest they name. Its last line names the
  ownership rule that judged the change set: the task id from `--task-id`/`TEND_TASK_ID`, the
  task id recovered from `<tend-root>/orchestrator.sqlite` (opened `mode=ro`, which reads the
  rows tend's WAL holds; `immutable=1` only when no `-wal` lies beside it or its directory
  cannot be written, and then the line says it was read immutably; **with an assumed
  schema**: read that line on the smoke task, as for §4 step 5), or the single-stem rule,
  with the reason the lookup found no task.
- **What travels.** `cf run` sends `corpus.sqlite` (and the database the workers search, when the
  run is blind or planted) and the candidates directory, and checks the database's sha once at the
  verify stage. The run's own sources (their rendered directories, and nothing of any other run's)
  travel only with `--include-source-text`: the gate fails closed without their layers, so the
  verify stage checks each of the run's sources on the host as the gate reads it (its `layer.txt`
  against `gate.json`'s `layer_sha256`, and the `formulas.json` and `paragraphs.json` beside it)
  and fails while one is missing, and `--execute` without the flag names the sources it expects
  to find there already. Every `cf run` below therefore carries `--host` and
  `--include-source-text`. **A locate run needs a spend cap**: `cf run --execute` refuses one
  without a `budget.max_cost`, and has no `--no-budget-cap` escape for it.
- **The host-side directories**, all in the tend root and outside every worktree: `usage/` (each
  agent call's JSON envelope, a cost cross-check), `attempts/<task-id>.json` (genuine reviewer
  rejections only; the third makes the worker bridge write `UNRESOLVED` documents with
  `unresolved.by: attempt_cap`, which the reviewer bridge approves mechanically), `gate-log/gate.jsonl`
  (one line per gate call, by rule, with its `ownership` line, `task_id_from`, `caller` and
  `changed_files`; the watchdog's gate-failure rate and the bench's rejection rates count only the
  `changed_files: true` lines that are not `pre_merge`, tend's validation calls
  (`caller: validation`), never an agent's `--findings` check).
- **The worker's tools.** The worker allowlist adds the two corpus host tools by absolute path,
  pinned to the run's database (`CF_EXTRA_TOOLS`), and an inventory worker's passage-id tool.
- **Bringing it home.** `cf harvest <run-id>` lands `corpus/<run-id>/<task-id>/<source-id>.json`,
  re-gates the whole corpus with the run's own staged gate (`runs/<run-id>/tend-root/gate/`,
  checked against `run.yaml` `locate.gate_files`, named by hash in the receipt) and
  `gate.local.json`, counts `UNRESOLVED` documents and
  attempt-capped ones separately in the receipt, and also brings the host's gate log (and, for a
  campaign shard, its usage export and canary scan) to `runs/<run-id>/host/`. Then
  `cf tally --locate --run <run-id>`.
- **What travels, for a blind or planted run.** The gate always checks the full `corpus.sqlite`
  (`gate.json` `corpus_db`); the workers search the tools database (`tools_corpus_db`: the full one,
  `corpus.blind.sqlite` for `--blind`, or a planted file given to `cf plan-locate --tools-corpus-db`).
  `cf run` transfers the tools database too when it is another file, and checks both shas at the
  verify stage. A P run of the bench is planned that way with the bench's planted database
  (`references/locate-bench.md` §7).
- **A kept run's sourceset (Tier A).** The first kept run plans from a sourceset that is
  created new and filled only by `adopt` from the consumer feed's first delivery, whose every
  pin carries its bytes and the producer's `layer_sha256`:
  `cf sourceset create <id>` (no `--from`), `cf sourceset adopt <id> --from <delivery>/pins.jsonl
  --consumer <consumer>`, `cf sourceset render <id>`, then `cf sourceset status <id> --verify`.
  It is **never** seeded with `cf sourceset create <id> --from wpm3` (the smoke runs' sourceset),
  nor from any sourceset whose sources were adopted before, and the delivery is never adopted
  into one that was. **Why:** a source adopted from a pin that carried no producer sha records
  an `adopted_from` without one, a recorded `adopted_from` never changes, and `create --from`
  copies it, so a seeded source can never be given the producer's `layer_sha256`:
  adopting the delivery into it only lists each such pin under
  `commands.adopt.layer_sha256_not_recorded`, and no render of it is ever held to the
  producer's text layer. Adopted into a new sourceset, each pin's `layer_sha256` is recorded in
  its `adopted_from`, `render` refuses by name a source whose layer is not that sha, and
  `state.json`'s `parity_refusals` is the parity measurement on the run's real pages
  (`references/locate-documents.md` §7).
  TODO (due before contributors or runs 2–10; not built): `adopt` fills a missing
  `adopted_from.layer_sha256` once, in a new sourceset version, and only when the pin's bytes
  hash to the recorded `raw_sha256`; it records the delivering pins file's sha beside it (a new
  optional `adopted_from` key, documented in `references/locate-documents.md` §7); and when a
  layer already exists it compares at once and writes `parity_refusals`. Until then this
  procedure is the only way a source is held to the producer's layer.
- **Shards.** More than 500 tasks is a campaign:
  1. Run a **pilot** first: a small locate run taken to the smoke stage with `cf run <pilot>
     --host <alias> --i-have-consent --include-source-text --execute`, watched through worker →
     gate → reviewer → merge, then raised with `--stage raise --smoke-verified --execute`, which
     records `run.smoke_verified`. Harvest it (`cf harvest <pilot> --execute`), which brings its
     agent envelopes home to `runs/<pilot>/host/envelopes/`, and read its p90 cost per merged
     task and gate-failure rate from the harvest.
  2. Plan each shard with `cf plan-locate` (each with its own `--max-cost`), then
     `cf campaign plan <id> --shard <r1> --shard <r2> … --host <alias> --pilot <pilot>` with the
     pilot's p90s. The watchdog estimates a call that left no priced envelope (a timeout, a
     signal) at `--unpriced-usd-per-hour <usd>` until its shard has a priced envelope of its
     own; without the flag the plan measures the pilot's highest priced dollars per hour over
     its harvested envelopes that carry a duration, and says which rate it took (or that it
     has none). `cf campaign plan` stamps `locate.campaign` into each shard's `run.yaml` and
     refuses a shard stamped by another campaign; run again, it reads every shard's `run.yaml`
     afresh and keeps the thresholds, the flag's rate, pilot, transcript directories and
     canaries it is not given, and measures a pilot's rate again.
     Give `--transcript-dirs` (or set `hosts.<alias>.transcript_dirs` in `workspace.yaml`) to have
     each shard's transcripts exported and pruned, and `--canaries <host path>` to have them scanned
     for the bench's label canaries; each is an absolute host path or `~/…` (the host's home).
     **What the sequencer takes from those directories:** a file that appeared there since a shard
     first started and names the shard's tend root, entrypoint or gate directory (an agent works in
     a worktree of the run, from prompts that name its gate) is that shard's: it is scanned,
     exported into the shard's `usage-<run-id>.jsonl` and then **deleted**. A new file that names
     none of them (another session of the same user, another run) is left alone, so an agent CLI's
     per-user directory works; but nothing else of the run itself may write there.
  3. Take every shard through `cf run <shard> --host <alias> --i-have-consent
     --include-source-text --execute --stage verify`, which transfers and verifies it and launches
     nothing. `cf campaign render <id>` refuses a campaign without a smoke-verified pilot or
     without a rate for unpriced calls (harvest the pilot and plan again, or give
     `--unpriced-usd-per-hour`), and a
     shard whose `run.yaml` no longer matches the campaign's record of it (its cap, task count,
     limits or `locate.campaign` stamp): a shard re-planned since is planned into the campaign
     again with `cf campaign plan`, then rendered.
  4. Run the two commands `cf campaign render <id>` prints: the transfer (`ssh <alias> mkdir -p
     <remote root>/campaigns/<id> && rsync …`, which copies `campaigns/<id>/host/` there) and the
     start of its `sequencer.sh` under `nohup` (or start it in a terminal multiplexer). It takes a
     lock (`sequencer.lock`), so a second start exits 3, and nothing it launches holds that lock,
     so a sequencer that was killed can be started again. It refuses a root that already has a
     live `tend run` (a killed sequencer's tend run lives on, watched by its watchdog to the end;
     a watchdog still on a root when its shard is launched again is stopped first), keys its state
     by run id, and runs shard k+1 only after shard k's `tend run` ends. Beside each
     shard its model-free watchdog drains the root (`tend-control drain`) when the shard's spend
     passes its cap, or when the gate-failure rate or the cost per merged task passes the
     configured multiple of the pilot's p90. After each shard it scans that shard's transcripts for
     canaries (`canary_scan.py` writes `{"scanned": N, "hits": [<file>, …], "canaries": K,
     "canary_sha256": [<sha256 of each canary>]}`; `cf bench score` refuses a report that does not
     list its bench's canary), exports their usage and prunes them; a canary hit keeps them, and a
     scan that cannot run keeps them unexported for the next attempt. A drain, a reason to drain,
     a canary hit, a scan that could not run, a watchdog that died during the shard or an
     unreadable watchdog record stops the campaign (`STOPPED`; `sequencer.log` and
     `shard-<run-id>.stopped` say why). `CF_TEND_CMD`, `CF_TRANSCRIPT_DIRS`, `CF_CANARIES` and
     `CF_WATCH_INTERVAL` override what was rendered; a transcript directory or canaries file that
     is not there stops the sequencer before any shard.
  5. **Resuming.** Remove `STOPPED` and start the sequencer again. A shard that was interrupted
     (a reboot, a killed sequencer) keeps the transcript baseline it first took, and its earlier
     attempt's transcripts are scanned, exported and pruned before anything is relaunched. A
     shard is **not relaunched** while one of its canary reports holds a hit (a report with a hit
     is never replaced: a later scan is kept beside it in `canary-<run-id>.attempts/`), or while
     the watchdog, run once before the launch, already finds a reason to drain it: relaunching it
     would only spend past the cap and stop again. A shard stopped **at its cap** goes on with a
     higher one (re-plan the shard with `cf plan-locate … --max-cost <usd>`, run `cf campaign
     plan` and `cf campaign render` again, and transfer). A higher cap passes nothing else: a
     gate-failure rate or a cost per merged task past its threshold is the same at any cap, and
     a **canary hit** is passed only by accepting the shard as it stands. `CF_ACCEPT=<run-id>`
     (several, separated by commas) settles what the shard left, marks it done, writing into
     `shard-<run-id>.done` why it had stopped, and the campaign moves on to the next shard; its
     canary report stays as it is for the harvest. While the canary scan of what the shard left
     cannot run, the accept is refused and the campaign stops again (its transcripts stay
     unexported); accept it once the scan works. Only a shard that has started (stopped or
     interrupted) is accepted: an id naming a shard that never started, or no shard of the
     campaign, is ignored with a line in `sequencer.log`, and that shard runs.
  6. Harvest each shard as it finishes (`cf harvest <shard> --execute`); `cf campaign status <id>`
     reads the shards' receipts.

  The sequencer needs, on the host: bash 4.4 or later (`mapfile -d`), GNU `comm` and `sort` (with
  `-z`), `flock`, `pgrep`, `grep`, `gzip`, `python3` and rsync.

## 10. An informalization, from `init` to publish

The informalize mode (`docs/proposals/informalize.md`) runs the audit backwards: from a Lean
library at a commit and one theorem it writes the source. Its fleet phases use the tend root,
the bridge and the harvest of §3 to §7 unchanged; what follows is the **whole** sequence, in
order, with what runs where. Nothing below is done by hand except the six approvals of §13 of
that document and publishing itself.

**Two machines.** Every `cf` command runs on the operator's machine — it never runs `lake`,
`lean` or elan. The host runs two jobs you start by hand from printed lines (the library build
of I1 and the canonical PDF of I6, plus the optional Verso build of the hovers), and the fleet
phases that `cf run` drives. The host needs elan and lake, git, nice and ionice; TeX
(`pdflatex`, `latexmk`, `kpsewhich`) with every package of `tex_packages`; and, for I6,
poppler (`pdftotext`, `pdftoppm`, `pdfinfo`) — without poppler `canonical_pdf.sh` stops after
the two builds with exit 4. The operator's machine needs python3 and git, and nothing else.

**`workspace.yaml`.** Beside §2's settings, `hosts.<host>.remote_root` must be that host's own
entry and its last part must be `informalize-<id>`: `build-command`, `pdf-command` and
`fetch-*` refuse the workspace-wide root, the operator's home and another id's folder, so one
host entry serves one informalize id and a second id needs that entry changed first.

  1. **I0, the operator's machine.**

     ```sh
     cf informalize init <id> --library <https URL> --commit <40 hex> --module <M> \
         --theorem <T> --prefix <P> --text-license <SPDX> --title <title> --author <name> \
         [--reference "<citation line>"]
     ```

     writes `informalize/<id>/informalize.yaml` and creates the output repository
     `informalize/<id>/repo/` — a git repository of its own that the workspace's git ignores,
     with the LaTeX runtime, the project files and `LICENSE`. `cf informalize sync <id>
     --check` says whether its runtime is this checkout's; every later stage refuses while it
     is not.

  2. **I1, the host.** `cf informalize build-command <id> --host <host>` runs nothing: it
     writes `library/to-host/` and prints the three lines that copy the drivers there and
     start the build, probe, canary and dependency dump, detached. Run them, wait for
     `library/host/DONE` to hold `0` (the printed `tail`/`cat` line watches it), then, on the
     operator's machine:

     ```sh
     cf informalize fetch-library <id> --host <host> --execute --i-have-consent
     cf informalize scope <id>
     ```

     The fetch checks what came back before it replaces anything, writes the library's license
     into `informalize.yaml` and the commit's time into `informal/informalize.json`; `scope`
     writes `scope.json` and commits `informal/declarations.json`.

  3. **I3 to I5, the fleet.** One tend run per phase, each `cf plan-informalize <id> --phase P
     … --i-have-consent` → `cf run` (dry run, then `--execute --stage smoke`, then
     `--stage raise`) → `cf harvest` → `cf informalize apply <id> --run <run>`, exactly as §3
     to §7 describe. The phases are `items`, `conventions`, `draft`, then `attack`, `judge`
     (`--from-run <attack run>`), `edit` (`--from-run <judge run>`) and `attack --sections
     touched` again until no `wrong` or `imprecise` finding stands. Two approvals come after
     the items phase and before any drafting:

     ```sh
     cf informalize approve <id> items --by "<name>" --in-v1 all
     cf informalize reference-checklist <id>
     cf informalize approve <id> references --by "<name>"
     ```

     The planner refuses a later phase while either is absent or stale. A phase's spend cap is
     a precondition, and the watchdog `cf run` starts at the smoke stage is what enforces it
     (§4): `cf run <run> --status` reads its last record.

  4. **I6, the canonical PDF.** On the operator's machine:

     ```sh
     cf informalize pdf-command <id> --host <host>
     ```

     It runs nothing. It refuses an output repository with an uncommitted change, a harvested
     phase nobody applied and a runtime that is not this checkout's; otherwise it writes the
     repository's files at HEAD into `pdf/<commit12>/to-host/src/` and prints the lines that
     rsync that tree to the host and start `informal/tools/canonical_pdf.sh --source-commit
     <sha>` there, detached. Run them. On the host the script builds twice from clean into
     `a/` and `b/`, refuses two PDFs with different sha256, runs `extract_markers.py`,
     `check_items.py --strict` and `proof_sheets.py`, writes `pdf-receipt.json`, and writes
     `DONE` last, on a failure too. When `DONE` holds `0`:

     ```sh
     cf informalize fetch-pdf <id> --host <host> --execute --i-have-consent
     ```

     brings the PDF, `markers.json`, the receipt and the proof sheets home to
     `pdf/<commit12>/`, checks each against the others, commits `informal/markers.json` and
     writes `stages/I6.json`. Then **read the proof sheets and the PDF** and sign:

     ```sh
     cf informalize approve <id> proof-sheets --by "<name>"
     ```

  5. **I7, the annotations and the site.** With the release asset URL you will publish the
     PDF's exact bytes at (the file need not be up yet):

     ```sh
     cf informalize overlay <id> --pdf-url <the release asset URL>
     cf informalize check-site <id>
     ```

     `overlay` writes the overlay's configuration, the landing page `site/index.html` and the
     two workflows from `informalize.yaml` — no file of the overlay is edited by hand — exports
     the annotations with `cf export informal-markers`, syncs and checks the overlay, commits
     each step, and seeds `.lake/overlay-pdf/` with the canonical PDF so the site builds before
     the URL is live. Run it where **poppler** is: the export titles each notion with the text
     the canonical PDF prints inside its boxes, and without `pdftotext` and `pdfinfo` it falls
     back to the words of the `\fcnotion` with their TeX markup stripped and says so in the
     export report (`counts.titles_not_printed`). `check-site` runs the companion's own `check_generated.py` and
     `pdf-overlay/tools/serve.py --build-only`, refuses if either left a tracked file changed,
     reads the built site back against its receipt and writes `stages/I7.json`. Then look at
     `site/` in a browser over a loopback static server — the PDF loads, a box opens a card
     with its declaration, its statement and its link at the commit, the console is clean —
     and sign:

     ```sh
     cf informalize approve <id> publish --by "<name>"
     ```

  6. **The hovers, optional, after step 5.** `cf informalize verso-command <id> --host <host>`
     needs the annotation bundle step 5 wrote; it prints the lines that build the library's
     literate HTML with Verso on the host and cut `verso.json` from it. Bring that file back
     with the printed `rsync` line into `pdf-overlay/verso.json`, then run `cf informalize
     overlay` and `check-site` again: the overlay commits it, untracked though it is, and the
     site build publishes the hovers in place of the empty file. Sign the publish approval
     again afterwards, since it pins the commit before them.

  7. **Publishing, the maintainer's alone.** Create the repository, push, upload the canonical
     PDF's **exact bytes** as the release asset at the URL the annotations pin, and turn on
     Pages (workflow artifact; the `pages` workflow step 5 wrote deploys it). Until that URL is
     live the site builds from the seeded cache, and nothing else does.

`cf informalize status <id>` prints where the id stands — each stage's receipt, the item table,
each approval (absent, current, or stale and why) — and the command that comes next. Every
command may be re-run: a stage whose inputs are unchanged writes nothing new.

## 11. What stays manual, permanently

- ssh access and any credential or login on the host;
- the hosted-agent profile login;
- the box owner's consent for a heavy job (recorded, not assumed);
- obtaining the book PDF and pinning its sha;
- watching the first task end-to-end before limits are raised;
- deciding the billing path and setting any gateway-side cap;
- every command in `workflows/` — the Workflow tool is invoked inside a session, not from `cf`, so
  `cf adjudicate` / `report` / local `run` print the script path and the `args`, and the result is
  handed back with `cf adjudicate --transcribe <result.json> --from {adjudicate,challenge}` (a
  local fleet's journal comes back through `cf harvest <id> --journal <wf dir>`). No command
  ingests a result: `cf ingest` is pipeline stage S0, which pins a PDF (`docs/pipeline.md`), and
  this line once sent a stuck reader to it for results;
- opening, merging or reporting anything to a repository owner;
- in the informalize mode (§10): the six approvals of `docs/proposals/informalize.md` §13,
  the two host jobs started from printed lines, and publishing — the repository, the release
  asset at the URL the annotations pin, and Pages.

---

## 12. Sources

| claim | source |
|---|---|
| pipeline, smoke test, steering commands, budget-before-concurrency, harvest snippet | the five run roots' `RUNBOOK.md` (identical across the three differential variants) |
| the bridge architecture, zero usage counters, the normaliser's necessity | the same file's "ADDENDUM — the run as actually launched" |
| worktree reclamation and the pre-run state | the same file's "NOTE 2026-09-07" |
| per-variant differences (gate kinds, prompt add-ons, bridge, concurrency) | diffing `config.yaml`, `prompts/*`, `bin/*` and each root's own `verify_findings.py` across the five roots |
| 16 concurrent hosted sessions per account; 10–15 min/task; 16–24 h for 763 tasks; coordinate heavy jobs | the docstring-tag companion's `HANDOFF.md` §8 |
| the gate fails closed without the book text, and the two hard-wired paths | same, §8; the v1 corpus's own `verify_findings.py` |
| the local API-key bridge failure (20 requests, 18 retries, HTTP 404 on no credit) | the design critique, risks |
| gate out of tree + changed-files check; read-only source mounts; host allowlist and recorded consent | the design critique, gaps 3 and 13, risks 2–3 |
