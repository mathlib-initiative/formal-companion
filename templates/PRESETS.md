# Run presets — exactly how the five original runs differed

Recorded by diffing the five orchestration roots and entrypoints retrieved from `ffmain`
(`~/tao-diff-tend`, `~/tao-diff-tend-pdf`, `~/tao-diff-tend-gpt`, `~/tao-attack-vac-tend`,
`~/tao-attack-hunt-tend` and their entrypoints `~/tao-diff-run{,-pdf,-gpt}`,
`~/tao-attack-{vac,hunt}`). Everything below is a diff result, not a reconstruction: where a
file was byte-identical across roots the md5 is noted once.

The skill's preset table (`scripts/plan/presets.py`) reproduces these five and adds the v2
corrections required by the design critique (§"What v2 changes", last section).

## 1. The five original presets

| preset | entrypoint | orchestration root | worker bridge | model | book access | KINDS (gate) | VERDICTS (gate) | witness | workers/reviewers |
|---|---|---|---|---|---|---|---|---|---|
| `differential-text` | `tao-diff-run` | `tao-diff-tend` | `agentbox host claude --profile jack` | Claude Code default (unrecorded) | `book.official.txt` only | CONTENT, PAGE_ANCHOR, VACUITY, SUSPECTED_ERRATA, COVERAGE | AGREE, CONVENTION_ONLY, CONTENT_DELTA, SINGLE_SOURCE_OK, SINGLE_SOURCE_DELTA | not required | 8 / 5 |
| `differential-pdf` | `tao-diff-run-pdf` | `tao-diff-tend-pdf` | `agentbox host claude --profile jack` | Claude Code default | text **+ page images** `~/book/bookpages/p-NNN.png` | same as above | same as above | not required | 6 / 4 |
| `differential-gpt` | `tao-diff-run-gpt` | `tao-diff-tend-gpt` | `agentbox host codex -- exec -m gpt-5.6-sol` | `gpt-5.6-sol` | text only | same as above | same as above | not required | 6 / 4 |
| `attack-vac` | `tao-attack-vac` | `tao-attack-vac-tend` | `agentbox host codex -- exec -m gpt-5.6-sol` | `gpt-5.6-sol` | text only | **CONTENT, VACUITY, SUSPECTED_ERRATA** | the five **+ NO_DEFECT_FOUND + NO_WEAKNESS_FOUND** | required (prompt + reviewer) | 5 / 3 |
| `attack-hunt` | `tao-attack-hunt` | `tao-attack-hunt-tend` | `agentbox host claude --profile jack` | Claude Code default | text **+ page images** | CONTENT, VACUITY, SUSPECTED_ERRATA | five + NO_DEFECT_FOUND + NO_WEAKNESS_FOUND | required | 5 / 3 |

`PAGE_ANCHOR` and `COVERAGE` are *rejected by the gate* in both attack presets — the comment in
the attack `verify_findings.py` is literally `# citation findings banned in the attack runs`.

## 2. The gate: only two lines differ

`scripts/verify_findings.py` is byte-identical across the three differential entrypoints
(md5 `ca976440…`) and byte-identical across the two attack entrypoints (md5 `f4cc21a8…`).
The whole diff is:

```diff
-VERDICTS = {"AGREE", "CONVENTION_ONLY", "CONTENT_DELTA", "SINGLE_SOURCE_OK", "SINGLE_SOURCE_DELTA"}
-KINDS = {"CONTENT", "PAGE_ANCHOR", "VACUITY", "SUSPECTED_ERRATA", "COVERAGE"}
+VERDICTS = {"AGREE", "CONVENTION_ONLY", "CONTENT_DELTA", "SINGLE_SOURCE_OK", "SINGLE_SOURCE_DELTA", "NO_DEFECT_FOUND", "NO_WEAKNESS_FOUND"}
+KINDS = {"CONTENT", "VACUITY", "SUSPECTED_ERRATA"}  # citation findings banned in the attack runs
```

Everything else was shared and is therefore preset-independent: `BOOK = ~/book/book.official.txt`
(hard-wired, now `gate.json.book.text`), `ROOT = <entrypoint>`, `SEV = {high, medium, low}`,
`norm()` (NFKC, ligatures, curly quotes, dash classes, then non-alphanumerics → space, lowercase),
`quote_ok()` (≥25 normalised chars; verbatim, else a ≥60%-word contiguous window, minimum 6 words),
required keys `item, book_located, matches, disagreements`, `book_located` must be true,
`book_passage_quote` and every `disagreement.book_quote` must pass `quote_ok`, every
`match.verdict ∈ VERDICTS`, every `disagreement.kind ∈ KINDS`, `severity ∈ SEV`, and every
`unfaithful_decls` entry must match `^(ours|teorth|adam):\S` (hard-wired repo list, now
`gate.json.repos`).

Note what the original gate did **not** do, and what v2 adds: no witness check (the attack runs'
witness requirement was prompt- and reviewer-enforced only), no edition stamp, no per-kind rules, no
declaration-name check, no changed-files check, and the gate ran from `scripts/` **inside the
worktree the worker may commit to**.

## 3. Prompts: one skeleton, four appended blocks

`worker.md` (md5 `b19c6541…`) and `worker-revision.md` (md5 `91300e12…`) are byte-identical in all
five roots. `worker-system.md`, `reviewer-system.md` and `reviewer.md` are the same base text plus
zero or more appended blocks — no base text was ever edited between presets:

| block | text | worker-system | reviewer-system | reviewer.md |
|---|---|---|---|---|
| `page_images` | "## You can SEE the page (run 2 only)" — `pdftotext` drops notation; images at `~/book/bookpages/p-NNN.png`, NNN = printed + 16, read with the Read tool; quotes still come from the text file | `differential-pdf`, `attack-hunt` | `differential-pdf`, `attack-hunt` | — |
| `approve_guidance` | "## When to APPROVE (this was missing, and its absence stalled the run)" — refutation is the posture, not the outcome; a coverage omission is a note, not a rejection | — | `differential-gpt`, `attack-vac` | — |
| `citation_ban` | "## This run does NOT accept citation findings" — PAGE_ANCHOR/COVERAGE rejected; every disagreement carries a concrete witness; `NO_DEFECT_FOUND`/`NO_WEAKNESS_FOUND` is a good answer | `attack-vac`, `attack-hunt` | `attack-vac`, `attack-hunt` | — |
| `final_line` | "## Final line (required)" — end output with exactly `VERDICT: approve` / `VERDICT: request_changes`, parsed mechanically | — | — | `differential-gpt`, `attack-vac` (the two **codex** bridges) |

Two consequences worth keeping:

* `attack-hunt`'s reviewer never got the `approve_guidance` block although `attack-vac`'s did;
  that asymmetry is an accident of the run order, not a design choice. The skill's presets give
  every reviewer the approve guidance and PRESETS records the deviation here.
* `final_line` tracks the **bridge**, not the preset: `normalize_result.py` for codex has to parse
  a verdict out of free text because codex does not reliably write `.tend/agent_result.json`.

## 4. Bridges

`bin/worker-agent.sh`, `bin/reviewer-agent.sh` and `bin/normalize_result.py` come in exactly two
flavours:

* **claude** (md5 `e44308a0…` / `b5d946a7…` / `d1b72eb4…`) — `differential-text`,
  `differential-pdf`, `attack-hunt`:
  `agentbox host claude --profile jack -- -p "$PROMPT" --append-system-prompt "$SYS"
  --dangerously-skip-permissions --output-format json`
* **codex** (md5 `7c419805…` / `58ccba1e…` / `0fcc3bc2…`) — `differential-gpt`, `attack-vac`:
  `printf '' | agentbox host codex --profile jack -- exec --skip-git-repo-check -m gpt-5.6-sol
  --dangerously-bypass-approvals-and-sandbox -C "$PWD" "$SYS\n\n---\n\n$TASKP"`
  (system prompt concatenated because codex has no `--append-system-prompt`).

Both flavours share the rework fix: if `$PWD/.tend/discussion.md` is non-empty the last 6000 bytes
are appended as "THIS IS A REVISION"; without it a rejected task loops forever on the original
prompt. Both write `$PWD/.tend/agent_result.json` via `normalize_result.py <role> <out> <raw>`.

The bridge scripts themselves are owned by `scripts/run/` in this skill; `make_tend_root.py` only
copies them into `<tend-root>/bin/` and points `config.yaml` at them.

## 5. `config.yaml` and `.tend/*.yaml`

All five `config.yaml` files differ **only** in `entrypoint`, the two `bin/*.sh` paths and the two
concurrency numbers. Shared: `workspace_mirror.enabled: false`, `validation_commands` ==
`pre_merge_validation_commands` == `[python3, scripts/verify_findings.py]`,
`merge_target_branch: main`, `merge_validation_worktree: true`, `seed_worktree_build: false`,
`batched_merge: true`, `skip_build_validation_for_task_only_merges: false`,
`budget: {currency: USD}`, `agent_oom_score_adj: 750`, `cleanup_closed_worktrees: true`.

The four `.tend/*.yaml` files are byte-identical across all five roots (`worker-agent.yaml`
`52cd1eb8…`, `reviewer-agent.yaml` `49e5205d…`, both `*-cfg.yaml` `168ffa35…`): they configure the
**`tend-agent` backend that none of the five runs actually used** — `anthropic/claude-sonnet-4-5`,
reasoning effort `low`, `max_output_tokens: 32768`, 7200 s wall limit, compaction on, worker tools
`ls read_file grep glob write_file edit_file copy_lines bash`, reviewer tools the same minus the
writers, output tool `final_result` with schema `worker_contribution` / `review_verdict`. They are
kept as templates because `--bridge tend-agent` is a supported fallback.

## 6. Tasks

| | differential presets | attack presets |
|---|---|---|
| slug | `corollary-1_3_14` (kind dashed, numbers underscored) | `corollary-1-3-14` (all dashes) |
| title | `"DIFFERENTIAL: Corollary 1.3.14"` / `"SINGLE-SOURCE: Corollary 1.5.10"` | `"hunt: Corollary 1.3.14"` / `"vac: Corollary 1.5.10"` |
| priority | `default` for every task | `max` / `high` / `default` from the prioritiser |
| universe | book items ∩ ≥1 repo (382 tasks: 224 DIFFERENTIAL + 158 SINGLE-SOURCE) | every target the audited companion formalises, In-text items included (763) |
| body | "Adjudicate book item X across the N repositories…" + per-repo declaration list + printed page | "Adversarially hunt…"/"Probe … for WEAKNESS" + declarations + peers-are-reference-only + the four probe questions (vac) |

Non-numbered attack targets slug through the same rule: `In-text, book p. 10` →
`in-text-book-p-10` (138 such files in `findings-hunt/`). The two slug styles are a preset field
(`slug_style: dash | dash_underscore`); new runs use `dash`.

Priority rule, recovered exactly from `targets.prioritised.json` (200 max / 223 high / 340 default):

```
key kinds = {Definition, Lemma, Theorem, Corollary, Proposition, In-text}
max     : single-source AND kind ∈ key kinds
high    : (not single-source AND kind ∈ key kinds) OR (single-source AND section ∈ late sections)
default : everything else            late sections = {1.5, 1.6, 1.7}
```

## 7. What v2 changes (and why)

1. **The gate runs out of tree.** `validation_commands` and `pre_merge_validation_commands` call
   `python3 <tend-root>/gate/verify_findings.py --gate-config <tend-root>/gate/gate.json`, an
   absolute path outside every worktree, so a worker that commits an edited `scripts/` cannot
   weaken its own gate. A second command, `check_changed_files.py`, refuses any contribution that
   touches anything but `findings/<task>.json` and `tasks/<task>.yaml`.
2. **`gate.json` replaces the hard-wired constants**: a `sources` block (one entry per source the
   run touches: text path, `sha256`, `text_sha256`, page offset, images dir), the repo list,
   KINDS, VERDICTS, severities, witness requirement and the per-kind rule matrix. The gate picks
   the text by the document's `source_id`, so one run can span more than one source without ever
   checking a quote against the wrong book.
3. **Per-kind gate matrix.** CONTENT/VACUITY need a quote *and* (in attack mode) a witness;
   SUSPECTED_ERRATA needs a quote; COVERAGE needs an item and no quote; PROXY needs `bridge` and
   `closure` evidence and no quote; PAGE_ANCHOR is mechanical-only (`source: anchor_check`) and
   is not offered to any LLM preset.
4. **`faithful` is a list**, `witness` is an object, `source_id` + `source_sha` are stamped, and
   `finding_id = sha256(run_id, source_id, item, kind, sorted(unfaithful_decls))` — five parts,
   because an item is the pair `(source_id, item)`.
5. **`NO_WEAKNESS_FOUND` is folded into `NO_DEFECT_FOUND`** by `convert_v1.py`; the v2 vocabulary
   has one no-defect verdict. The gate still accepts `NO_WEAKNESS_FOUND` when a legacy preset
   lists it, so the baseline corpus validates unchanged.
6. **Legacy presets** `differential-text-legacy` / `attack-legacy` exist for exactly one purpose:
   validating the 2,672-file baseline after `convert_v1.py`. They keep PAGE_ANCHOR/COVERAGE in
   KINDS and both no-defect verdicts, and they do not require a witness object beyond the lifted
   one. New runs never use them.
7. **Worktree budgeting**: `--max-open-tasks N` chains `depends_on` so tend holds at most N
   worktrees at once (the original run created 382 worktrees / 1.1 GB in 11 minutes).
8. **Host allowlist and consent**: `plan-run` refuses a host absent from `workspace.yaml`'s
   allowlist and records `consent.given` (from `--i-have-consent`) in `run.yaml`; `run` refuses to
   start without it.

## 8. Where the conventions come from

No preset and no template in this repo contains a companion's house conventions: both target
repositories are private, and an unconfirmed convention list is the single biggest source of the
~40% first-pass false-positive rate. `plan/render_prompts.py` reads each companion's
`companions/<name>/conventions.md` (contract: `templates/conventions.md.example`) and fills:

| conventions.md section | prompt slot |
|---|---|
| `1. Total functions and junk values` | the per-repo `CONVENTION_ONLY` bullet, and the junk-value question in the **vac** probe (`total_functions_sentence`) |
| `2. Standing type and structure conventions` | the per-repo bullet, and the shared "Any repo:" line (de-duplicated union across companions of the same source) |
| `3. Scope` | the per-repo bullet ("stops at §1.4" style notes) |
| `4. Sorry policy` | the per-repo bullet (permanent exercise sorries) |
| `7. Known-and-accepted deltas` | reserved for the adjudication COMMON block |

A section left at the shipped `(example) …` placeholders counts as empty, and a companion with no
recorded conventions renders as "no conventions recorded yet — treat every delta as content until
the owner confirms one" rather than as a fabricated list.

## 9. Task ids and what the gate command looks like

```
tasks/<slug>.yaml            differential: corollary-1_3_14   attack: corollary-1-3-14
findings/<slug>.json         one per task; nothing else may change
validation_commands          python3 <remote_root>/<run-id>-gate/verify_findings.py \
                               --gate-config <remote_root>/<run-id>-gate/gate.json \
                               --changed-files --base main
```

Two items in the item table are worth keeping in mind when reading a converted corpus:

* two of the 1,146 baseline differential rows (`findings-pdf/exercise-1_6_6`,
  `findings-gpt/exercise-1_6_44`) are `PAGE_ANCHOR` findings with an **empty**
  `unfaithful_decls` — which is exactly why routing keys on that field and why the v2
  `mechanical` preset requires both a declaration and a `{claimed, actual}` page;
* the attack presets' `worker-system.md` is the differential one plus two appended blocks, so an
  attack worker is still told it is looking at N independent formalizations. The per-task
  description is what restricts it to the audited companion ("as rendered by `ours`"; "Peers …
  reference only, do NOT report defects in them").
