# Trigger evals

`trigger.json` is eleven prompts, six that should make this skill fire and five
that should not. They are **data**, not a test: running them calls a model, and
nothing in `tests/` ever does. `tests/test_rf_docs.py` only checks the file's
shape, so a malformed eval set fails fast without costing anything.

## Why this exists

546 unit tests said the Python was right and nothing said the *skill* was
right. The two properties that decide whether a second user gets a correct
audit — does it fire when it should, and does it stay out of the way when it
should not — were unmeasured, and the frontmatter description is the only thing
that decides either. These prompts are the measuring stick for a change to that
description.

The five negatives are the interesting half. The nearest misses are "review
this PR", "fix this proof" and "run the linter": they carry *Lean*,
*formalization* and *review*, and firing on them costs a user ~5k tokens of
skill for a question that needed none.

## Running them

The `skill-creator` plugin ships the runner. From this repo's root:

```sh
SC=<the skill-creator plugin>/skills/skill-creator
python3 "$SC/scripts/run_eval.py" \
    --eval-set evals/trigger.json \
    --skill-path . \
    --runs-per-query 3 \
    --verbose
```

Each prompt runs in its own `claude -p` session with only this skill's name and
description in context, and the runner reports a trigger rate per query plus a
pass/fail against `--trigger-threshold` (0.5 by default). A rate is not a
verdict: three runs of eleven prompts is a small sample, so read the per-query
rates, not only the summary line.

To compare a candidate description against the shipped one without editing
`SKILL.md`:

```sh
python3 "$SC/scripts/run_eval.py" --eval-set evals/trigger.json --skill-path . \
    --description "<the candidate>" --runs-per-query 3
```

`$SC/scripts/improve_description.py` does the same thing as a search, which is
the honest way to settle the open questions below.

## Two open questions

The shipped description ends with a negative clause — "not a proof checker, not
a Lean linter and not a fixer". That clause is two-sided: the skill-creator
guidance says Claude tends to *under*-trigger skills and advises pushier
descriptions, so a sentence telling it when not to fire could cost more than
the over-triggers it prevents. Nobody has measured it here.

If you change that clause, measure it: run the set with and without, on a
train/test split of the queries, and keep whichever scores better on the ones
you held out. Record the result in `CHANGELOG.md`, because "we thought it read
better" is exactly the kind of unmeasured claim this repo exists to catch in
other people's work.

The second is the **informalize clause**, added with that mode
(`docs/proposals/informalize.md`): the description gained a sentence about
writing the informal text of a Lean theorem, and lost "reads repositories" and
a few other words to stay under the router's 1,024 characters. Nobody has
run the set with and without it, so whether the new clause earns its room — and
whether `write-up-from-lean`, the prompt it exists for, fires at all — is
unmeasured. It is the first thing to settle on the next eval run.

## Adding a case

One object per prompt, in the shape the runner reads:

```json
{"id": "short-slug", "query": "what a user would actually type",
 "should_trigger": true, "why": "one sentence: what this case is protecting"}
```

`query` and `should_trigger` are what the runner uses; `id` and `why` are for
the reader. Write the query as a person would say it, not as documentation
would: a prompt that quotes this skill's own vocabulary back at it measures
nothing.
