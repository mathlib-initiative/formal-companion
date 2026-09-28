# M0a inputs (exported read-only, 2026-09-27)

- `s17b-usage.jsonl.gz`: one JSON object per assistant message of the section 1.7
  re-run fleet (102 Claude Code transcripts, 5,207 messages): `{model, usage}` only,
  no text, paths, hosts or users. At Opus 5 list prices (input $5, output $25,
  cache write $6.25, cache read $0.50 per million tokens, the table in issue #11)
  the rows sum to $358.10, the recorded total.
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
