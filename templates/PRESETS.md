# Run presets — exactly how the five original runs differed

Recorded by diffing the five orchestration roots and entrypoints of the v1 campaign
(`tao-diff-tend`, `tao-diff-tend-pdf`, `tao-diff-tend-gpt`, `tao-attack-vac-tend`,
`tao-attack-hunt-tend` and their entrypoints `tao-diff-run{,-pdf,-gpt}`,
`tao-attack-{vac,hunt}`, each a directory in the run host's home). Everything below is a diff result, not a reconstruction: where a
file was byte-identical across roots the md5 is noted once.

The skill's preset table (`scripts/plan/presets.py`) reproduces these five and adds the v2
corrections required by the design critique (§"What v2 changes", last section).

## 1. The five original presets

| preset | entrypoint | orchestration root | worker bridge | model | book access | KINDS (gate) | VERDICTS (gate) | witness | workers/reviewers |
|---|---|---|---|---|---|---|---|---|---|
| `differential-text` | `tao-diff-run` | `tao-diff-tend` | `agentbox host claude --profile <profile>` | Claude Code default (unrecorded) | `book.official.txt` only | CONTENT, PAGE_ANCHOR, VACUITY, SUSPECTED_ERRATA, COVERAGE | AGREE, CONVENTION_ONLY, CONTENT_DELTA, SINGLE_SOURCE_OK, SINGLE_SOURCE_DELTA | not required | 8 / 5 |
| `differential-pdf` | `tao-diff-run-pdf` | `tao-diff-tend-pdf` | `agentbox host claude --profile <profile>` | Claude Code default | text **+ page images** `~/book/bookpages/p-NNN.png` | same as above | same as above | not required | 6 / 4 |
| `differential-gpt` | `tao-diff-run-gpt` | `tao-diff-tend-gpt` | `agentbox host codex -- exec -m gpt-5.6-sol` | `gpt-5.6-sol` | text only | same as above | same as above | not required | 6 / 4 |
| `attack-vac` | `tao-attack-vac` | `tao-attack-vac-tend` | `agentbox host codex -- exec -m gpt-5.6-sol` | `gpt-5.6-sol` | text only | **CONTENT, VACUITY, SUSPECTED_ERRATA** | the five **+ NO_DEFECT_FOUND + NO_WEAKNESS_FOUND** | required (prompt + reviewer) | 5 / 3 |
| `attack-hunt` | `tao-attack-hunt` | `tao-attack-hunt-tend` | `agentbox host claude --profile <profile>` | Claude Code default | text **+ page images** | CONTENT, VACUITY, SUSPECTED_ERRATA | five + NO_DEFECT_FOUND + NO_WEAKNESS_FOUND | required | 5 / 3 |

`PAGE_ANCHOR` and `COVERAGE` are *rejected by the gate* in both attack presets — the comment in
the attack `verify_findings.py` is literally `# citation findings banned in the attack runs`.

## 2. The gate: only two lines differ

The gate script is byte-identical across the three differential entrypoints
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
| `page_images` | "## You can SEE the page (run 2 only)" — `pdftotext` drops notation; images at `<sources>/<id>/images/p-NNN.png`, NNN = printed + offset, read with the Read tool; quotes still come from the text file | `differential-pdf`, `attack-hunt` | `differential-pdf`, `attack-hunt` | — |
| `approve_guidance` | "## When to APPROVE (this was missing, and its absence stalled the run)" — refutation is the posture, not the outcome; a coverage omission is a note, not a rejection | — | `differential-gpt`, `attack-vac` | — |
| `citation_ban` | "## This run does NOT accept citation findings" — names the citation kinds THIS run's gate rejects | every preset whose `kinds` omit PAGE_ANCHOR or COVERAGE | same | — |
| `witness_required` | "every disagreement must carry a concrete witness" | `attack-vac`, `attack-hunt` | same | — |
| `final_line` | "## Final line (required)" — end output with exactly `VERDICT: approve` / `VERDICT: request_changes`, parsed mechanically | — | — | `differential-gpt`, `attack-vac` (the two **codex** bridges) |

Three of those are no longer hand-set flags, because a hand-set flag drifted from the gate
it was supposed to describe:

* `citation_ban` is `preset.banned_citation_kinds` — the members of {PAGE_ANCHOR, COVERAGE}
  missing from `preset.kinds`. In v1 only the attack presets carried the block, so both
  differential presets shipped a prompt inviting "a wrong page anchor" their own gate does
  not list as a kind (22 of 40 batch-1 differential findings were pagination noise);
* the CONTENT_DELTA list's page-anchor clause, and the single-source task's "is the page
  anchor correct?" question, come from the same `preset.kinds`;
* the "how to finish" instruction is `preset.finish_channel`, one per bridge:
  `final_result` for `tend-agent`, `.tend/agent_result.json` for the agentbox bridges,
  a last-line JSON object for `claude-p`. One rendered prompt set used to name all three,
  including a `final_result` tool the agentbox bridges do not expose.

Two figures in those prompts are SLOTS, never literals: the CONVENTION-vs-CONTENT
calibration sentence and the page-anchor mix that justifies the citation ban. Both are
rendered from the workspace's own `ledger/{accepted,refuted}.*.jsonl` when it holds
adjudicated rows, and otherwise state the imported figure **with its sample size and the
campaign it came from** (4 of 10 adjudicated rows, and 37-vs-4 page anchors, on one chapter
of one book). A rendered prompt may not assert a measurement of a corpus nobody measured.

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
  `agentbox host claude --profile <profile> -- -p "$PROMPT" --append-system-prompt "$SYS"
  --dangerously-skip-permissions --output-format json`
* **codex** (md5 `7c419805…` / `58ccba1e…` / `0fcc3bc2…`) — `differential-gpt`, `attack-vac`:
  `printf '' | agentbox host codex --profile <profile> -- exec --skip-git-repo-check -m gpt-5.6-sol
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
`pre_merge_validation_commands` == `[python3, <the entrypoint's gate script>]`,
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

Those model ids are no longer literals in two modules. `scripts/plan/models.py` is the one
table: the tend-agent backend's defaults and the codex presets' pinned `gpt-5.6-sol` live
there, a workspace `models:` entry of the same key overrides it, and `cf plan-run` resolves
the run's model through it — recording the id **and** `model_source` (`--model`,
`workspace.models.<key>`, `preset.<name>`) in `run.yaml`. The resolved id reaches the agents
two ways, because the backends read different things: `model_name` in the generated
`.tend/worker-agent.yaml`, and `CF_MODEL` in `bin/<role>-agent.sh` for every bridge. Before
this, `--model` changed `run.yaml` and nothing else.

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
   run touches: text path, `sha256`, `text_sha256`, page offset, images dir, and `item_pages` —
   `{item: pdf_page}` of the pinned text, which is how a quote is bound to the page its item is
   stated on), the repo list,
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
   **Who computes it.** `require_finding_id` (the field must be PRESENT) is on for `mechanical`
   alone, whose rows this tool writes. No runnable preset demands it: no prompt teaches an agent
   the hash, no helper ships beside the gate, and a required field nobody can produce would fail
   every task. The corpus's authority for the id is `harvest.tally`'s recompute, which keeps a
   disagreeing stored value as `finding_id_stored` and reports it as a
   `finding_id_mismatches` row. What the gate does enforce on **every** preset is that an id that
   IS written is the right one: it recomputes and refuses a present-but-wrong `finding_id`, so the
   recovered run that stamped `"finding_id": "00…00"` on every row would now be rejected rather
   than stored verbatim. That is **every** preset including the two `*-legacy` ones, where
   `cf harvest --no-gate` is the escape for an import whose stored ids are known-wrong — so a
   re-import of such a corpus lands with `harvest.tally`'s `finding_id_stored` /
   `finding_id_mismatches` record rather than being refused. A value that is present but is not a
   string is refused the same way, by name, because reading a worker-written field means its type is
   the worker's too. Measured over the imported baseline: 347 of 347 defect rows carry an id
   after `convert_v1.py` and all 347 match, so nothing already landed is refused.
5. **`NO_WEAKNESS_FOUND` is folded into `NO_DEFECT_FOUND`** by `convert_v1.py`; the v2 vocabulary
   has one no-defect verdict. The gate still accepts `NO_WEAKNESS_FOUND` when a legacy preset
   lists it, so the baseline corpus validates unchanged.
6. **Legacy presets** `differential-legacy` / `attack-legacy` are IMPORT-ONLY: they are the
   gate for a v1 corpus adopted with `cf import-corpus --preset <that> --gate` (which is how
   the 2,672-file baseline validates after `convert_v1.py`). They keep PAGE_ANCHOR/COVERAGE in
   KINDS and both no-defect verdicts, and they do not require a witness object beyond the lifted
   one. `cf plan-run` refuses them and its refusal names that purpose; no fleet ever runs them.
7. **Worktree budgeting**: `--max-open-tasks N` chains `depends_on` so tend holds at most N
   worktrees at once (the original run created 382 worktrees / 1.1 GB in 11 minutes).
8. **Host allowlist and consent**: `plan-run` refuses a host absent from `workspace.yaml`'s
   allowlist and records `consent.given` (from `--i-have-consent`) in `run.yaml`; `run` refuses to
   start without it.

## 8. Where the conventions come from

No preset and no template in this repo contains a companion's house conventions: both target
repositories are private, and an unconfirmed convention list is the single biggest source of the
~40% first-pass false-positive rate (4 of 10 adjudicated rows, one chapter of one book —
`references/method.md` S4; the figure never travels without that denominator, and neither does the
rendered prompt's version of it). `plan/render_prompts.py` reads each companion's
`companions/<name>/conventions.md` (contract: `templates/conventions.md.example`) and fills:

| conventions.md section | prompt slot |
|---|---|
| `1. Total functions and junk values` | the per-repo `CONVENTION_ONLY` bullet, and the junk-value question in the **vac** probe (`total_functions_sentence`) |
| `2. Standing type and structure conventions` | the per-repo bullet, and the shared "Any repo:" line (de-duplicated union across companions of the same source) |
| `3. Scope` | the per-repo bullet ("stops at §1.4" style notes) |
| `4. Sorry policy` | the per-repo bullet (permanent exercise sorries) |
| `5. Naming` / `6. Errata disposition` | no prompt slot: the WHOLE file is inlined into the adjudication COMMON block, and a second copy could disagree with the first |
| `7. Known-and-accepted deltas` | the "refuted on sight — do not report these" block in `worker-system.md`, and the adjudication COMMON block |

A section left at the shipped `(example) …` placeholders counts as empty, and a companion with no
recorded conventions renders as "no conventions recorded yet — treat every delta as content until
the owner confirms one" rather than as a fabricated list.

One more prompt slot comes from the workspace rather than from a companion: `sources.<id>.prompt_examples`
in `workspace.yaml` supplies that source's own CONTENT_DELTA illustrations, and without it the
prompt uses the book-neutral defaults in `render_prompts.NEUTRAL_EXAMPLES` (a list of strings —
prose for the prompt, never a discovered fact, so it has no inference path and no `cf sources`
override).

**That state is now refused, not rendered.** `cf plan-run` stops when a companion's
`conventions.md` carries no `confirmed_by:` stamp, or when every section that feeds a
`CONVENTION_ONLY` bullet (1–4 above) is empty after the `(example)` bullets are dropped: the
prompt it would otherwise render puts the imported false-positive rate (4 of 10 adjudicated rows,
one chapter of one book) three lines above "treat every delta as content", and the actionable half
of that maximises the failure the rest of the design exists to suppress. Section 7 on its own does
not confirm a file — known-and-accepted deltas refute findings rather than licensing them, so a
file carrying only those still leaves the judgement unwritten, and the refusal names sections 1–4
rather than "every section". `--conventions-unconfirmed` plans anyway and is recorded in `run.yaml`
under `conventions.unconfirmed`, so the report can say the findings rest on a list nobody confirmed.

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

## 10. The three locate presets

Not runs of a companion: `cf plan-locate` plans them from a sourceset and a universe, and
`cf plan-run` refuses their names (`scripts/locate/presets.py`; `docs/proposals/wikipedia-locate.md`
§3). They render tend roots on the same bridge, gate directory and harvest, with the locate gate
(`scripts/gate/verify_locate.py`) in the validation commands and the locate prompts
(`templates/prompts/locate-*.md.tmpl`).

| preset | stage | task | kinds | workers / reviewers |
|---|---|---|---|---|
| `locate-inventory` | L3a | one source; its deterministic finders' passages pre-filled | `PASSAGE`, `UNRESOLVED` | 8 / 4 |
| `locate` | L3b | at most 8 `formal_math` passages of a frozen inventory, one source (`--subject-only`: one subject passage per article, bundled) | `LOCATION`, `ABSENCE`, `UNRESOLVED` | 8 / 4 |
| `locate-challenge` | L5 | about 20 records of harvested locate runs, by the §5 rules and a seeded stratified sample | `LOCATION`, `ABSENCE`, `UNRESOLVED` | 6 / 3 |

```
tasks/<id>.yaml              inv-<source-id> | loc-<source-id>-<k> | loc-subject-<kkkk> | chl-<kkkk>
findings/<id>/<source>.json  one per source of the task manifest; nothing else may change
gate/tasks/<id>.json         the task manifest, outside every worktree
validation_commands          python3 <remote_root>/<run-id>-gate/verify_locate.py \
                               --gate-config <remote_root>/<run-id>-gate/gate.json \
                               --changed-files --base main
```

The wrappers add `CF_EXTRA_TOOLS` (worker only: the corpus tools, and the passage-id tool in an
inventory run), `CF_TASK_MANIFESTS`, `CF_ATTEMPT_CAP=3` and `CF_FINDINGS_GLOB`. The tend reviewer
of a locate run checks form and the gate's output only; judgment lives in the challenge.
