# M0a inputs (exported read-only, 2026-09-27)

- `s17b-usage.jsonl.gz`: one JSON object per assistant **transcript line** of
  the section 1.7 re-run fleet (102 Claude Code transcripts, 5,207 lines):
  `{model, usage}` only, no text, paths, hosts or users. These rows are not
  responses. Claude Code writes one line per content block of a response and
  repeats the response's usage on each, and the export kept no message id, so
  a response with three content blocks is three identical rows here.
  - Summed line by line at Opus 5 list prices (input $5, output $25, cache
    write $6.25, cache read $0.50 per million tokens, the table in issue #11)
    the rows give $358.10, the recorded total. The recorded total was summed
    line by line the same way, so this match shows only that the same sum
    gives the same answer. It does not reproduce the run's cost from its
    transcripts.
  - 2,579 rows are exact copies of the row before. Collapsing each run of
    identical adjacent rows leaves 2,628 rows, which price to $168.91, about
    53% lower. That is the estimate of the per-response cost: the rows carry
    no id to collapse on, so it could merge two different responses with the
    same counts or miss a response whose lines are not adjacent. The
    transcript reader counts each response once, so on the retained
    transcripts it should give about this figure, but they are not available
    here and that re-read has not been done.
  - `tests/test_cost.py` (`TestTheS17bRowsAreContentBlockLines`) recomputes
    all of these figures from the rows.
- `codex-usage-shapes.json` and `codex-usage-sample.json`: the shapes of the
  token-count events in a Codex rollout log (`rollout-*.jsonl`), with every
  free string replaced by `<str>`. The usage object has `input_tokens`,
  `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`,
  `reasoning_output_tokens`, `total_tokens`; the model id sits beside it.
  Token-count events repeat the same counts several times in a row, so a reader
  must not sum duplicates blindly; check whether the event carries cumulative
  (`total_token_usage`) and per-turn (`last_token_usage`) blocks.
- Pre-fix tao-measure-lean commit: `d8cdfd5` (2026-09-14, the first-parent
  commit before PR #8's merge 96e64a9 of 2026-09-15, which opened the
  camera-ready fix stack #8, #9, #10, #4, #12, #13, #14).
